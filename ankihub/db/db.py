"""Code for managing the AnkiHub database. The AnkiHub database stores the state of AnkiHub decks
as they are on AnkiHub, unlike the Anki database which can contain local changes to the deck.
The AnkiHub database is updated when downloading updates from AnkiHub (this is done by the AnkiHubImporter).
The purpose of the database is for the add-on to have knowledge of the state of the decks on AnkiHub without having to
request this data from AnkiHub every time it is needed. This e.g. enables the add-on to partially work offline and
to only send the necessary data to AnkiHub when syncing.

Some differences between data stored in the AnkiHub database and the Anki database:
- The type of stored objects is different, e.g. the Anki database stores anki.notes.Note objects,
    while the AnkiHub database stores ankihub_client.NoteInfo objects.
- decks, notes and note types can be missing from the Anki database or be modified.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from anki.utils import ids2str, join_fields, split_fields

from .. import LOGGER
from ..ankihub_client import Field, NoteInfo, suggestion_type_from_str
from ..common_utils import local_media_names_from_html
from .db_utils import DBConnection
from .exceptions import IntegrityError

# This tag can be added to a note to cause media files to be synced even if the
# media file is in an media disabled field.
# It does NOT only work for images, but the name is kept for backwards compatibility.
MEDIA_DISABLED_FIELD_BYPASS_TAG = "AnkiHub_ImageReady"


def attach_ankihub_db_to_anki_db_connection() -> None:
    if aqt.mw.col is None:
        LOGGER.info("The collection is not open. Not attaching AnkiHub DB.")
        return

    if not is_ankihub_db_attached_to_anki_db():
        aqt.mw.col.db.execute(
            f"ATTACH DATABASE ? AS {ankihub_db.database_name}",
            str(ankihub_db.database_path),
        )
        LOGGER.info("Attached AnkiHub DB to Anki DB connection")


def detach_ankihub_db_from_anki_db_connection() -> None:
    if aqt.mw.col is None:
        LOGGER.info("The collection is not open. Not detaching AnkiHub DB.")
        return

    if is_ankihub_db_attached_to_anki_db():
        # Liberal use of try/except to ensure we always try to detach and begin a new
        # transaction.
        try:
            # close the current transaction to avoid a "database is locked" error
            aqt.mw.col.save(trx=False)
        except Exception:
            LOGGER.info("Failed to close transaction.")

        try:
            aqt.mw.col.db.execute(f"DETACH DATABASE {ankihub_db.database_name}")
            LOGGER.info("Detached AnkiHub DB from Anki DB connection")
        except Exception:
            LOGGER.info("Failed to detach AnkiHub database.")

        # begin a new transaction because Anki expects one to be open
        aqt.mw.col.db.begin()

        LOGGER.info("Began new transaction.")


def is_ankihub_db_attached_to_anki_db() -> bool:
    if aqt.mw.col is None:
        return False

    result = ankihub_db.database_name in [
        name for _, name, _ in aqt.mw.col.db.all("PRAGMA database_list")
    ]
    return result


@contextmanager
def attached_ankihub_db():
    attach_ankihub_db_to_anki_db_connection()
    try:
        yield
    finally:
        detach_ankihub_db_from_anki_db_connection()


class _AnkiHubDB:

    # name of the database when attached to the Anki DB connection
    database_name = "ankihub_db"
    database_path: Optional[Path] = None

    def execute(self, *args, **kwargs) -> List:
        return self.connection().execute(*args, **kwargs)

    def list(self, *args, **kwargs) -> List:
        return self.connection().list(*args, **kwargs)

    def scalar(self, *args, **kwargs) -> Any:
        return self.connection().scalar(*args, **kwargs)

    def first(self, *args, **kwargs) -> Optional[Tuple]:
        return self.connection().first(*args, **kwargs)

    def dict(self, *args, **kwargs) -> Dict[Any, Any]:
        rows = self.connection().execute(*args, **kwargs, first_row_only=False)
        result = {row[0]: row[1] for row in rows}
        return result

    def setup_and_migrate(self, db_path: Path) -> None:
        self.database_path = db_path

        journal_mode = self.scalar("pragma journal_mode=wal")
        if journal_mode != "wal":
            LOGGER.warning("Failed to set journal_mode=wal")

        if self.schema_version() == 0:
            self._setup_notes_table()
            self._setup_note_types_table()
            self.execute("PRAGMA user_version = 8")
        else:
            from .db_migrations import migrate_ankihub_db

            migrate_ankihub_db()

    def _setup_notes_table(self) -> None:
        """Create the notes table."""
        with self.connection() as conn:
            conn.execute(
                """
                CREATE TABLE notes (
                    ankihub_note_id STRING PRIMARY KEY,
                    ankihub_deck_id STRING,
                    anki_note_id INTEGER UNIQUE,
                    anki_note_type_id INTEGER,
                    mod INTEGER,
                    guid TEXT,
                    fields TEXT,
                    tags TEXT,
                    last_update_type TEXT
                );
                """
            )
            conn.execute("CREATE INDEX ankihub_deck_id_idx ON notes (ankihub_deck_id);")
            conn.execute("CREATE INDEX anki_note_id_idx ON notes (anki_note_id);")
            conn.execute("CREATE INDEX anki_note_type_id ON notes (anki_note_type_id);")
            LOGGER.info("Created notes table")

    def _setup_note_types_table(self) -> None:
        """Create the note types table."""
        with self.connection() as conn:
            conn.execute(
                """
                CREATE TABLE notetypes (
                    anki_note_type_id INTEGER PRIMARY KEY,
                    ankihub_deck_id STRING NOT NULL,
                    name TEXT NOT NULL,
                    note_type_dict_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX notetypes_ankihub_deck_id_idx ON notetypes (ankihub_deck_id);"
            )
            LOGGER.info("Created note types table")

    def schema_version(self) -> int:
        result = self.scalar("PRAGMA user_version;")
        return result

    def connection(self) -> DBConnection:
        result = DBConnection(conn=sqlite3.connect(ankihub_db.database_path))
        return result

    def upsert_notes_data(
        self, ankihub_did: uuid.UUID, notes_data: List[NoteInfo]
    ) -> Tuple[Tuple[NoteInfo, ...], Tuple[NoteInfo, ...]]:
        """Upsert notes data to the AnkiHub DB.
        If a note with the same Anki nid already exists in the AnkiHub DB then the note will not be inserted
        Returns a tuple of (NoteInfo objects that were inserted / updated, NoteInfo objects that were skipped)
        """

        # Check if all note types used by notes exist in the AnkiHub DB before inserting
        mids_of_notes = set([note_data.mid for note_data in notes_data])
        mids_in_db = set(self.note_types_for_ankihub_deck(ankihub_did))
        missing_mids = [mid for mid in mids_of_notes if mid not in mids_in_db]
        if missing_mids:
            raise IntegrityError(
                "Can't insert notes data because the following note types are "
                f"missing from the AnkiHub DB: {missing_mids}"
            )

        upserted_notes: List[NoteInfo] = []
        skipped_notes: List[NoteInfo] = []
        with self.connection() as conn:

            for note_data in notes_data:
                conflicting_ah_nid = conn.first(
                    """
                    SELECT ankihub_note_id FROM notes
                    WHERE anki_note_id = ?
                    AND ankihub_note_id != ?
                    """,
                    note_data.anki_nid,
                    str(note_data.ah_nid),
                )
                if conflicting_ah_nid:
                    skipped_notes.append(note_data)
                    continue

                fields = join_fields(
                    [
                        field.value
                        for field in sorted(
                            note_data.fields, key=lambda field: field.order
                        )
                    ]
                )
                note_data.tags = [tag for tag in note_data.tags if tag is not None]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO notes (
                        ankihub_note_id,
                        ankihub_deck_id,
                        anki_note_id,
                        anki_note_type_id,
                        fields,
                        tags,
                        guid,
                        last_update_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    str(note_data.ah_nid),
                    str(ankihub_did),
                    note_data.anki_nid,
                    note_data.mid,
                    fields,
                    " ".join(note_data.tags),
                    note_data.guid,
                    note_data.last_update_type.value[0]
                    if note_data.last_update_type is not None
                    else None,
                )
                upserted_notes.append(note_data)

        return (tuple(upserted_notes), tuple(skipped_notes))

    def remove_notes(self, ah_nids: Sequence[uuid.UUID]) -> None:
        """Removes notes from the AnkiHub DB"""
        with self.connection() as conn:
            conn.execute(
                f"""
                DELETE FROM notes WHERE ankihub_note_id IN
                {uuids2str(ah_nids)}
                """,
            )

    def transfer_mod_values_from_anki_db(self, notes_data: Sequence[NoteInfo]):
        """Takes mod values for the notes from the Anki DB and saves them to the AnkiHub DB.

        Should always be called after importing notes or exporting notes after
        the mod values in the Anki DB have been updated.
        (The mod values are used to determine if a note has been modified in Anki since it was last imported/exported.)
        """
        with self.connection() as conn:
            for note_data in notes_data:
                mod = aqt.mw.col.db.scalar(
                    "SELECT mod FROM notes WHERE id = ?", note_data.anki_nid
                )

                conn.execute(
                    "UPDATE notes SET mod = ? WHERE ankihub_note_id = ?",
                    mod,
                    str(note_data.ah_nid),
                )

    def reset_mod_values_in_anki_db(self, anki_nids: List[NoteId]) -> None:
        # resets the mod values of the notes in the Anki DB to the
        # mod values stored in the AnkiHub DB
        nid_mod_tuples = self.execute(
            f"""
            SELECT anki_note_id, mod from notes
            WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )

        for nid, mod in nid_mod_tuples:
            aqt.mw.col.db.execute(
                "UPDATE notes SET mod = ? WHERE id = ?",
                mod,
                nid,
            )
        aqt.mw.col.save()

    def ankihub_nid_exists(self, ankihub_nid: uuid.UUID) -> bool:
        # It's possible that an AnkiHub nid does not exists after calling insert_or_update_notes_data
        # with a NoteInfo that has the AnkkiHub nid if a note with the same Anki nid already exists
        # in the AnkiHub DB but in different deck.
        result = self.scalar(
            """
            SELECT 1 FROM notes WHERE ankihub_note_id = ? LIMIT 1
            """,
            str(ankihub_nid),
        )
        return bool(result)

    def note_data(self, anki_note_id: NoteId) -> Optional[NoteInfo]:
        # The AnkiHub note type of the note has to exist in the Anki DB, otherwise this will fail.
        result = self.first(
            f"""
            SELECT
                ankihub_note_id,
                anki_note_id,
                anki_note_type_id,
                tags,
                fields,
                guid,
                last_update_type
            FROM notes
            WHERE anki_note_id = {anki_note_id}
            """,
        )
        if result is None:
            return None

        ah_nid, anki_nid, mid, tags, flds, guid, last_update_type = result
        field_names = [
            field["name"] for field in aqt.mw.col.models.get(NotetypeId(mid))["flds"]
        ]
        return NoteInfo(
            ah_nid=uuid.UUID(ah_nid),
            anki_nid=anki_nid,
            mid=mid,
            tags=aqt.mw.col.tags.split(tags),
            fields=[
                Field(
                    name=field_names[i],
                    value=value,
                    order=i,
                )
                for i, value in enumerate(split_fields(flds))
            ],
            guid=guid,
            last_update_type=suggestion_type_from_str(last_update_type)
            if last_update_type
            else None,
        )

    def anki_nids_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NoteId]:
        result = self.list(
            """
            SELECT anki_note_id FROM notes
            WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )
        return result

    def ankihub_dids(self) -> List[uuid.UUID]:
        result = [
            uuid.UUID(did)
            for did in self.list("SELECT DISTINCT ankihub_deck_id FROM notes")
        ]
        return result

    def ankihub_did_for_anki_nid(self, anki_nid: NoteId) -> Optional[uuid.UUID]:
        did_str = self.scalar(
            f"""
            SELECT ankihub_deck_id FROM notes
            WHERE anki_note_id = {anki_nid}
            """
        )

        if not did_str:
            return None

        result = uuid.UUID(did_str)
        return result

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        did_strs = self.list(
            f"""
            SELECT DISTINCT ankihub_deck_id FROM notes
            WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        result = [uuid.UUID(did) for did in did_strs]
        return result

    def are_ankihub_notes(self, anki_nids: List[NoteId]) -> bool:
        notes_count = self.scalar(
            f"""
            SELECT COUNT(*) FROM notes WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        return notes_count == len(set(anki_nids))

    def ankihub_nid_for_anki_nid(self, anki_note_id: NoteId) -> Optional[uuid.UUID]:
        nid_str = self.scalar(
            """
            SELECT ankihub_note_id FROM notes
            WHERE anki_note_id = ?
            """,
            anki_note_id,
        )
        if nid_str is None:
            return None

        result = uuid.UUID(nid_str)
        return result

    def anki_nids_to_ankihub_nids(
        self, anki_nids: List[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        ah_nid_for_anki_nid = self.dict(
            f"""
            SELECT anki_note_id, ankihub_note_id FROM notes
            WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        result = {NoteId(k): uuid.UUID(v) for k, v in ah_nid_for_anki_nid.items()}

        not_existing = set(anki_nids) - set(result.keys())
        result.update({nid: None for nid in not_existing})

        return result

    def ankihub_nids_to_anki_nids(
        self, ankihub_nids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, NoteId]:
        anki_nid_for_ah_nid = self.dict(
            f"""
            SELECT ankihub_note_id, anki_note_id FROM notes
            WHERE ankihub_note_id IN {uuids2str(ankihub_nids)}
            """
        )
        result = {uuid.UUID(k): NoteId(v) for k, v in anki_nid_for_ah_nid.items()}

        not_existing = set(ankihub_nids) - set(result.keys())
        result.update({nid: None for nid in not_existing})

        return result

    def anki_nid_for_ankihub_nid(self, ankihub_id: uuid.UUID) -> Optional[NoteId]:
        note_id_str = self.scalar(
            """
            SELECT anki_note_id FROM notes WHERE ankihub_note_id = ?
            """,
            str(ankihub_id),
        )
        if note_id_str is None:
            return None

        result = NoteId(note_id_str)
        return result

    def remove_deck(self, ankihub_did: uuid.UUID):
        """Removes all data for the given deck from the AnkiHub DB"""
        with self.connection() as conn:
            conn.execute(
                """
                DELETE FROM notes WHERE ankihub_deck_id = ?
                """,
                str(ankihub_did),
            )
            conn.execute(
                """
                DELETE FROM notetypes WHERE ankihub_deck_id = ?
                """,
                str(ankihub_did),
            )

    def ankihub_deck_ids(self) -> List[uuid.UUID]:
        result = [
            uuid.UUID(did)
            for did in self.list("SELECT DISTINCT ankihub_deck_id FROM notes")
        ]
        return result

    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        result = self.scalar(
            "SELECT mod FROM notes WHERE ankihub_note_id = ?",
            str(ankihub_note_id),
        )
        return result

    def ankihub_dids_of_decks_with_missing_values(self) -> List[uuid.UUID]:
        # currently only checks the guid, fields and tags columns
        did_strs = self.list(
            "SELECT DISTINCT ankihub_deck_id FROM notes WHERE "
            "guid IS NULL OR "
            "fields IS NULL OR "
            "tags IS NULL"
        )
        result = [uuid.UUID(did) for did in did_strs]
        return result

    def media_names_for_ankihub_deck(
        self, ah_did: uuid.UUID, media_disabled_fields: Dict[int, List[str]]
    ) -> Set[str]:
        """Returns the names of all media files used in the notes of the given deck.
        param media_disabled_fields: a dict mapping note type ids to a list of field names
            that should be ignored when looking for media files.
        """
        result = set()
        # We get the media names for each note type separately, because
        # the disabled fields are note type specific.
        # Note: One note type is always only used in one deck.
        for mid in self.note_types_for_ankihub_deck(ah_did):
            disabled_field_names = media_disabled_fields.get(int(mid), [])
            result.update(
                self._media_names_on_notes_of_note_type(mid, disabled_field_names)
            )
        return result

    def media_names_exist_for_ankihub_deck(
        self, ah_did: uuid.UUID, media_names: Set[str]
    ) -> Dict[str, bool]:
        """Returns a dictionary where each key is a media name and the corresponding value is a boolean
        indicating whether the media file is referenced on any note in the given deck. This function is
        defined in addition to media_names_for_ankihub_deck to provide a more efficient way to check
        if some media files exist in the deck."""

        # This uses a different implementation when there are more than 30 media names to check
        # because the first method is fast up to a certain number of media names, but then becomes
        # very slow.
        if len(media_names) <= 30:
            result = self._media_names_exist_for_ankihub_deck_inner(ah_did, media_names)
        else:
            media_names_for_deck = self.media_names_for_ankihub_deck(ah_did, {})
            result = {name: name in media_names_for_deck for name in media_names}

        return result

    def _media_names_exist_for_ankihub_deck_inner(
        self, ah_did: uuid.UUID, media_names: Set[str]
    ) -> Dict[str, bool]:
        result = {}
        for media_name in media_names:
            result[media_name] = bool(
                self.scalar(
                    f"""
                    SELECT EXISTS(
                        SELECT 1 FROM notes
                        WHERE ankihub_deck_id = '{ah_did}'
                        AND (
                            fields LIKE '%src="{media_name}"%' OR
                            fields LIKE '%src=''{media_name}''%' OR
                            fields LIKE '%[sound:{media_name}]%'
                        )
                    )
                    """
                )
            )
        return result

    def _media_names_on_notes_of_note_type(
        self, mid: NotetypeId, disabled_field_names: List[str]
    ) -> Set[str]:
        """Returns the names of all media files used in the notes of the given note type."""
        if aqt.mw.col is None or aqt.mw.col.models.get(NotetypeId(mid)) is None:
            return set()

        field_names_for_mid = [
            field["name"] for field in aqt.mw.col.models.get(NotetypeId(mid))["flds"]
        ]
        disabled_field_ords = [
            field_names_for_mid.index(name)
            for name in disabled_field_names
            # We ignore fields that are not present in the note type.
            # This can happen if the user has remove the fields from the note type.
            if name in field_names_for_mid
        ]
        fields_tags_pairs = self.execute(
            f"""
            SELECT fields, tags FROM notes
            WHERE (
                anki_note_type_id = {mid} AND
                (fields LIKE '%<img%' OR fields LIKE '%[sound:%')
            )
            """
        )

        result = set()
        for fields_string, tags_string in fields_tags_pairs:
            fields = split_fields(fields_string)
            tags = set(tags_string.split(" "))
            for field_idx, field_text in enumerate(fields):
                # TODO: This ANKIHUB_MEDIA_ENABLED_TAG bypass is used to allow fields with
                # this specific tag to have the media files downloaded, despite the field being
                # marked as an media-disabled field. Decide whether to remove this.
                field_name = field_names_for_mid[field_idx]
                # Tags cant have spaces, so we replace spaces with underscores to make it possible to
                # reference a field name with spaces using a tag.
                bypass_media_disabled_tag = (
                    f"{MEDIA_DISABLED_FIELD_BYPASS_TAG}::{field_name.replace(' ', '_')}"
                )

                bypass = bypass_media_disabled_tag in tags
                if not bypass and field_idx in disabled_field_ords:
                    LOGGER.debug(
                        f"Blocking media download in [{field_name}] field without the tag [{bypass_media_disabled_tag}]"
                    )
                    continue

                if bypass:
                    LOGGER.debug(
                        f"Allowing media download in [{field_name}] field - note has tag [{bypass_media_disabled_tag}]",
                    )

                result.update(local_media_names_from_html(field_text))

        return result

    # note types
    def upsert_note_type(self, ankihub_did: uuid.UUID, note_type: NotetypeDict) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO notetypes (
                anki_note_type_id,
                ankihub_deck_id,
                name,
                note_type_dict_json
            ) VALUES (?, ?, ?, ?)
            """,
            note_type["id"],
            str(ankihub_did),
            note_type["name"],
            json.dumps(note_type),
        )

    def note_type_dict(
        self, ankihub_did: uuid.UUID, note_type_id: NotetypeId
    ) -> NotetypeDict:
        row = self.first(
            """
            SELECT note_type_dict_json
            FROM notetypes
            WHERE anki_note_type_id = ?
            AND ankihub_deck_id = ?
            """,
            note_type_id,
            str(ankihub_did),
        )
        if row is None:
            return None

        note_type_dict_json = row[0]
        result = NotetypeDict(json.loads(note_type_dict_json))
        return result

    def ankihub_note_type_ids(self) -> List[NotetypeId]:
        result = self.list("SELECT anki_note_type_id FROM notetypes")
        return result

    def is_ankihub_note_type(self, anki_note_type_id: NotetypeId) -> bool:
        result = self.scalar(
            """
            SELECT EXISTS(SELECT 1 FROM notetypes WHERE anki_note_type_id = ?)
            """,
            anki_note_type_id,
        )
        return result

    def note_types_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NotetypeId]:
        result = self.list(
            """
            SELECT anki_note_type_id FROM notetypes WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )
        return result

    def ankihub_did_for_note_type(
        self, anki_note_type_id: NotetypeId
    ) -> Optional[uuid.UUID]:
        did_str = self.scalar(
            """
            SELECT ankihub_deck_id FROM notetypes WHERE anki_note_type_id = ?
            """,
            anki_note_type_id,
        )
        if did_str is None:
            return None

        result = uuid.UUID(did_str)
        return result


ankihub_db = _AnkiHubDB()


def uuids2str(ankihub_nids: Sequence[uuid.UUID]) -> str:
    result = ", ".join(f"'{nid}'" for nid in ankihub_nids)
    result = f"({result})"
    return result
