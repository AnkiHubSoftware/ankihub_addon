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
from ..ankihub_client.models import DeckMedia
from ..common_utils import local_media_names_from_html
from ..settings import ANKI_INT_VERSION, ANKI_VERSION_23_10_00
from .db_utils import DBConnection
from .exceptions import IntegrityError
from .rw_lock import read_lock_context, write_lock_context


@contextmanager
def attached_ankihub_db():
    """Context manager that attaches the AnkiHub DB to the Anki DB connection and detaches it when the context exits.
    The purpose is to e.g. do join queries between the Anki DB and the AnkiHub DB through aqt.mw.col.db.execute().
    A lock is used to ensure that other threads don't try to access the AnkiHub DB through the _AnkiHubDB class
    while it is attached to the Anki DB.
    """
    with write_lock_context():
        _attach_ankihub_db_to_anki_db_connection()
        try:
            yield
        finally:
            _detach_ankihub_db_from_anki_db_connection()


@contextmanager
def detached_ankihub_db():
    """Context manager that ensures the AnkiHub DB is detached from the Anki DB connection while the context is active.
    The purpose of this is to be able to safely perform operations on the AnkiHub DB which require it to be detached,
    for example coyping the AnkiHub DB file.
    It's used by the _AnkiHubDB class to ensure that the AnkiHub DB is detached from the Anki DB while
    queries are executed through the _AnkiHubDB class.
    """
    with read_lock_context():
        yield


def _attach_ankihub_db_to_anki_db_connection() -> None:
    if aqt.mw.col is None:
        LOGGER.info("The collection is not open. Not attaching AnkiHub DB.")
        return

    if not is_ankihub_db_attached_to_anki_db():
        aqt.mw.col.db.execute(
            f"ATTACH DATABASE ? AS {ankihub_db.database_name}",
            str(ankihub_db.database_path),
        )
        LOGGER.info("Attached AnkiHub DB to Anki DB connection")


def _detach_ankihub_db_from_anki_db_connection() -> None:
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

        if ANKI_INT_VERSION < ANKI_VERSION_23_10_00:
            # db.begin was removed in Ani 23.10
            # begin a new transaction because Anki expects one to be open
            aqt.mw.col.db.begin()  # type: ignore

        LOGGER.info("Began new transaction.")


def is_ankihub_db_attached_to_anki_db() -> bool:
    if aqt.mw.col is None:
        return False

    result = ankihub_db.database_name in [
        name for _, name, _ in aqt.mw.col.db.all("PRAGMA database_list")
    ]
    return result


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
            self._setup_deck_media_table()
            self._setup_note_types_table()
            self.execute("PRAGMA user_version = 10")
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

    def _setup_deck_media_table(self) -> None:
        """Create the deck_media table."""
        with self.connection() as conn:
            conn.execute(
                """
                CREATE TABLE deck_media (
                    name TEXT NOT NULL,
                    ankihub_deck_id TEXT NOT NULL,
                    file_content_hash TEXT,
                    modified TIMESTAMP NOT NULL,
                    referenced_on_accepted_note BOOLEAN NOT NULL,
                    exists_on_s3 BOOLEAN NOT NULL,
                    download_enabled BOOLEAN NOT NULL,
                    PRIMARY KEY (name, ankihub_deck_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX deck_media_deck_hash ON deck_media (ankihub_deck_id, file_content_hash);"
            )
            LOGGER.info("Created deck_media table")

    def _setup_note_types_table(self, conn: Optional[DBConnection] = None) -> None:
        """Create the note types table."""
        sql = """
            CREATE TABLE notetypes (
                anki_note_type_id INTEGER NOT NULL,
                ankihub_deck_id STRING NOT NULL,
                name TEXT NOT NULL,
                note_type_dict_json TEXT NOT NULL,
                PRIMARY KEY (anki_note_type_id, ankihub_deck_id)
            );
        """
        if conn:
            conn.execute(sql)
        else:
            self.execute(sql)

        LOGGER.info("Created note types table")

    def schema_version(self) -> int:
        result = self.scalar("PRAGMA user_version;")
        return result

    def connection(self) -> DBConnection:
        result = DBConnection(
            conn=sqlite3.connect(ankihub_db.database_path),
            lock_context=detached_ankihub_db,
        )
        return result

    def upsert_notes_data(
        self, ankihub_did: uuid.UUID, notes_data: List[NoteInfo]
    ) -> Tuple[Tuple[NoteInfo, ...], Tuple[NoteInfo, ...]]:
        """Upsert notes data to the AnkiHub DB.
        If a note with the same Anki nid already exists in the AnkiHub DB then the note will not be inserted
        Returns a tuple of (NoteInfo objects that were inserted / updated, NoteInfo objects that were skipped)
        An IntegrityError will be raised if a note type used by a note does not exist in the AnkiHub DB.
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
        result = self.first(
            f"""
            SELECT
                ankihub_note_id,
                ankihub_deck_id,
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

        ah_nid, ah_did, anki_nid, mid, tags, flds, guid, last_update_type = result
        field_names = self._note_type_field_names(
            ankihub_did=ah_did, anki_note_type_id=mid
        )
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

    def anki_nid_to_ah_did_dict(
        self, anki_nids: Iterable[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        """Returns a dict mapping anki nids to the ankihub did of the deck the note is in.
        Not found nids are omitted from the dict."""
        result = self.dict(
            f"""
            SELECT anki_note_id, ankihub_deck_id FROM notes
            WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        result = {NoteId(k): uuid.UUID(v) for k, v in result.items()}
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
            conn.execute(
                """
                DELETE FROM deck_media WHERE ankihub_deck_id = ?
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

    # Media related functions
    def upsert_deck_media_infos(
        self,
        ankihub_did: uuid.UUID,
        media_list: List[DeckMedia],
    ) -> None:
        """Upsert deck media to the AnkiHub DB."""
        with self.connection() as conn:
            for media_file in media_list:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO deck_media (
                        name,
                        ankihub_deck_id,
                        file_content_hash,
                        modified,
                        referenced_on_accepted_note,
                        exists_on_s3,
                        download_enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    media_file.name,
                    str(ankihub_did),
                    media_file.file_content_hash,
                    media_file.modified,
                    media_file.referenced_on_accepted_note,
                    media_file.exists_on_s3,
                    media_file.download_enabled,
                )

    def downloadable_media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which can be downloaded for the given deck."""
        result = set(
            self.list(
                """
                SELECT name FROM deck_media
                WHERE ankihub_deck_id = ?
                AND referenced_on_accepted_note = 1
                AND exists_on_s3 = 1
                AND download_enabled = 1
                """,
                str(ah_did),
            )
        )
        return result

    def media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which are referenced on notes in the given deck."""
        fields_strings = self.list(
            """
            SELECT fields FROM notes
            WHERE (
                ankihub_deck_id = ? AND
                fields LIKE '%<img%' OR fields LIKE '%[sound:%'
            )
            """,
            str(ah_did),
        )

        result = {
            media_name
            for fields_string in fields_strings
            for media_name in local_media_names_from_html(fields_string)
        }
        return result

    def media_names_exist_for_ankihub_deck(
        self, ah_did: uuid.UUID, media_names: Set[str]
    ) -> Dict[str, bool]:
        """Returns a dictionary where each key is a media name and the corresponding value is a boolean
        indicating whether the media file is referenced on a note in the given deck.
        The media file doesn't have to exist on S3, it just has to referenced on a note in the deck.
        """
        placeholders = ",".join(["?" for _ in media_names])
        sql = f"""
            SELECT name FROM deck_media
            WHERE ankihub_deck_id = ?
            AND name IN ({placeholders})
            AND referenced_on_accepted_note = 1
            """

        names_in_db = set(
            self.list(
                sql,
                str(ah_did),
                *media_names,
            )
        )
        result = {name: name in names_in_db for name in media_names}
        return result

    def media_names_with_matching_hashes(
        self, ah_did: uuid.UUID, media_to_hash: Dict[str, Optional[str]]
    ) -> Dict[str, str]:
        """Returns a dictionary where each key is a media name and the corresponding value is the
        name of a media file in the given deck with the same hash.
        Media without a matching hash are not included in the result.

        Note: Media files with a hash of None are ignored as they can't be matched.
        """

        # Remove media files with None as hash because they can't be matched
        media_to_hash = {
            media_name: media_hash
            for media_name, media_hash in media_to_hash.items()
            if media_hash is not None
        }

        # Return early if no valid hashes remain
        if not media_to_hash:
            return {}

        placeholders = ",".join(["?" for _ in media_to_hash])
        hash_to_media = self.dict(
            f"""
            SELECT file_content_hash, name FROM deck_media
            WHERE ankihub_deck_id = ?
            AND file_content_hash IN ({placeholders})
            """,
            str(ah_did),
            *media_to_hash.values(),
        )

        result = {
            media_name: matching_media
            for media_name, media_hash in media_to_hash.items()
            if (matching_media := hash_to_media.get(media_hash)) is not None
        }
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
        result_str = self.scalar(
            """
            SELECT EXISTS(SELECT 1 FROM notetypes WHERE anki_note_type_id = ?)
            """,
            anki_note_type_id,
        )
        result = bool(result_str)
        return result

    def note_types_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NotetypeId]:
        result = self.list(
            """
            SELECT anki_note_type_id FROM notetypes WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )
        return result

    def ankihub_dids_for_note_type(
        self, anki_note_type_id: NotetypeId
    ) -> Optional[Set[uuid.UUID]]:
        """Returns the AnkiHub deck ids that use the given note type."""
        did_strings = self.list(
            """
            SELECT ankihub_deck_id FROM notetypes WHERE anki_note_type_id = ?
            """,
            anki_note_type_id,
        )
        if not did_strings:
            return None

        result = set(uuid.UUID(did_str) for did_str in did_strings)
        return result

    def _note_type_field_names(
        self, ankihub_did: uuid.UUID, anki_note_type_id: NotetypeId
    ) -> List[str]:
        """Returns the names of the fields of the note type."""
        result = [
            field["name"]
            for field in self.note_type_dict(
                ankihub_did=ankihub_did, note_type_id=anki_note_type_id
            )["flds"]
        ]
        return result


ankihub_db = _AnkiHubDB()


def uuids2str(ankihub_nids: Sequence[uuid.UUID]) -> str:
    result = ", ".join(f"'{nid}'" for nid in ankihub_nids)
    result = f"({result})"
    return result
