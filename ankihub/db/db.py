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
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from anki.utils import join_fields, split_fields

from .. import LOGGER
from ..ankihub_client import Field, NoteInfo, suggestion_type_from_str
from ..ankihub_client.models import DeckMedia as DeckMediaClientModel
from ..common_utils import local_media_names_from_html
from .db_utils import DBConnection
from .exceptions import IntegrityError
from .models import (
    AnkiHubNote,
    AnkiHubNoteType,
    DeckMedia,
    bind_peewee_models,
    get_peewee_database,
    set_peewee_database,
)


class _AnkiHubDB:
    database_path: Optional[Path] = None

    def execute(self, *args, **kwargs) -> List:
        return self.connection().execute(*args, **kwargs)

    def list(self, *args, **kwargs) -> List:
        return self.connection().list(*args, **kwargs)

    def scalar(self, *args, **kwargs) -> Any:
        return self.connection().scalar(*args, **kwargs)

    def first(self, *args, **kwargs) -> Optional[Tuple]:
        return self.connection().first(*args, **kwargs)

    def setup_and_migrate(self, db_path: Path) -> None:
        self.database_path = db_path

        set_peewee_database(db_path)

        journal_mode = self.scalar("pragma journal_mode=wal")
        if journal_mode != "wal":
            LOGGER.warning("Failed to set journal_mode=wal")  # pragma: no cover

        if self.schema_version() == 0:
            self._setup_notes_table()
            self._setup_deck_media_table()
            self._setup_note_types_table()
            self.execute("PRAGMA user_version = 10")
        else:
            from .db_migrations import migrate_ankihub_db

            migrate_ankihub_db()

        bind_peewee_models()

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
        return DBConnection()

    def upsert_notes_data(
        self, ankihub_did: uuid.UUID, notes_data: List[NoteInfo]
    ) -> Tuple[Tuple[NoteInfo, ...], Tuple[NoteInfo, ...]]:
        """
        Upsert notes data into the AnkiHub DB.

        If a note with the same Anki nid already exists in the AnkiHub DB, the note will be skipped.
        An IntegrityError will be raised if a note type used by a note does not exist in the AnkiHub DB.

        Returns:
            A tuple of (NoteInfo objects that were upserted, NoteInfo objects that were skipped)

        Post-conditions:
            After calling this function, you should:
            1. Upsert the notes which were upserted into the AnkiHub DB into the Anki DB.
            2. Call transfer_mod_values_from_anki_db to transfer the mod values of the upserted notes from the Anki DB
               to the AnkiHub DB.
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
        with get_peewee_database().atomic():
            for note_data in notes_data:
                # Check for conflicting note
                conflicting_ah_nid = (
                    AnkiHubNote.select(AnkiHubNote.ankihub_note_id)
                    .where(
                        AnkiHubNote.anki_note_id == note_data.anki_nid,
                        AnkiHubNote.ankihub_note_id != str(note_data.ah_nid),
                    )
                    .get_or_none()
                )

                if conflicting_ah_nid:
                    skipped_notes.append(note_data)
                    continue

                # Prepare fields and tags for insertion
                fields = join_fields(
                    [
                        field.value
                        for field in sorted(
                            note_data.fields, key=lambda field: field.order
                        )
                    ]
                )
                tags = " ".join([tag for tag in note_data.tags if tag is not None])

                # Insert or update the note
                (
                    AnkiHubNote.insert(
                        ankihub_note_id=str(note_data.ah_nid),
                        ankihub_deck_id=str(ankihub_did),
                        anki_note_id=note_data.anki_nid,
                        anki_note_type_id=note_data.mid,
                        fields=fields,
                        tags=tags,
                        guid=note_data.guid,
                        last_update_type=note_data.last_update_type.value[0]
                        if note_data.last_update_type is not None
                        else None,
                    )
                    .on_conflict_replace()
                    .execute()
                )

                upserted_notes.append(note_data)

        return tuple(upserted_notes), tuple(skipped_notes)

    def remove_notes(self, ah_nids: Sequence[uuid.UUID]) -> None:
        """Removes notes from the AnkiHub DB"""
        AnkiHubNote.delete().where(
            AnkiHubNote.ankihub_note_id.in_([str(uuid) for uuid in ah_nids])
        ).execute()

    def transfer_mod_values_from_anki_db(self, notes_data: Sequence[NoteInfo]):
        """Takes mod values for the notes from the Anki DB and saves them to the AnkiHub DB.

        Should always be called after importing notes or exporting notes after
        the mod values in the Anki DB have been updated.
        (The mod values are used to determine if a note has been modified in Anki since it was last imported/exported.)
        """
        with get_peewee_database().atomic():
            for note_data in notes_data:
                mod = aqt.mw.col.db.scalar(
                    "SELECT mod FROM notes WHERE id = ?", note_data.anki_nid
                )

                AnkiHubNote.update(mod=mod).where(
                    AnkiHubNote.ankihub_note_id == str(note_data.ah_nid)
                ).execute()

    def reset_mod_values_in_anki_db(self, anki_nids: List[NoteId]) -> None:
        # resets the mod values of the notes in the Anki DB to the
        # mod values stored in the AnkiHub DB
        nid_mod_tuples = (
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id, AnkiHubNote.mod)
            .where(AnkiHubNote.anki_note_id.in_(anki_nids))
            .tuples()
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
        return (
            AnkiHubNote.select()
            .where(AnkiHubNote.ankihub_note_id == str(ankihub_nid))
            .exists()
        )

    def note_data(self, anki_note_id: int) -> Optional[NoteInfo]:
        note = AnkiHubNote.get_or_none(AnkiHubNote.anki_note_id == anki_note_id)

        if not note:
            return None

        field_names = self._note_type_field_names(
            ankihub_did=note.ankihub_deck_id, anki_note_type_id=note.anki_note_type_id
        )

        return NoteInfo(
            ah_nid=uuid.UUID(note.ankihub_note_id),
            anki_nid=note.anki_note_id,
            mid=note.anki_note_type_id,
            tags=aqt.mw.col.tags.split(note.tags),
            fields=[
                Field(
                    name=field_names[i],
                    value=value,
                    order=i,
                )
                for i, value in enumerate(split_fields(note.fields))
            ],
            guid=note.guid,
            last_update_type=suggestion_type_from_str(note.last_update_type)
            if note.last_update_type
            else None,
        )

    def anki_nids_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NoteId]:
        query = AnkiHubNote.select(AnkiHubNote.anki_note_id).where(
            AnkiHubNote.ankihub_deck_id == str(ankihub_did)
        )
        return [note.anki_note_id for note in query]

    def ankihub_dids(self) -> List[uuid.UUID]:
        return [
            uuid.UUID(note.ankihub_deck_id)
            for note in AnkiHubNote.select(AnkiHubNote.ankihub_deck_id).distinct()
        ]

    def ankihub_did_for_anki_nid(self, anki_nid: NoteId) -> Optional[uuid.UUID]:
        note = AnkiHubNote.get_or_none(AnkiHubNote.anki_note_id == anki_nid)
        return uuid.UUID(note.ankihub_deck_id) if note else None

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        query = (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .where(AnkiHubNote.anki_note_id.in_(anki_nids))
            .distinct()
        )
        return [uuid.UUID(note.ankihub_deck_id) for note in query]

    def anki_nid_to_ah_did_dict(
        self, anki_nids: Iterable[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        """Returns a dict mapping anki nids to the ankihub did of the deck the note is in.
        Not found nids are omitted from the dict."""
        query = AnkiHubNote.select().where(AnkiHubNote.anki_note_id.in_(anki_nids))
        return {
            NoteId(note.anki_note_id): uuid.UUID(note.ankihub_deck_id) for note in query
        }

    def are_ankihub_notes(self, anki_nids: List[NoteId]) -> bool:
        notes_count = (
            AnkiHubNote.select().where(AnkiHubNote.anki_note_id.in_(anki_nids)).count()
        )
        return notes_count == len(set(anki_nids))

    def ankihub_nid_for_anki_nid(self, anki_note_id: NoteId) -> Optional[uuid.UUID]:
        result = (
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id)
            .where(AnkiHubNote.anki_note_id == anki_note_id)
            .scalar()
        )
        return uuid.UUID(result) if result else None

    def anki_nids_to_ankihub_nids(
        self, anki_nids: List[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        ah_nid_for_anki_nid = AnkiHubNote.select(
            AnkiHubNote.anki_note_id, AnkiHubNote.ankihub_note_id
        ).where(AnkiHubNote.anki_note_id.in_(anki_nids))
        result = {
            note.anki_note_id: uuid.UUID(note.ankihub_note_id)
            for note in ah_nid_for_anki_nid
        }

        not_existing = set(anki_nids) - set(result.keys())
        return result | dict.fromkeys(not_existing)

    def ankihub_nids_to_anki_nids(
        self, ankihub_nids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, NoteId]:
        ah_nid_for_anki_nid = AnkiHubNote.select(
            AnkiHubNote.ankihub_note_id, AnkiHubNote.anki_note_id
        ).where(AnkiHubNote.ankihub_note_id.in_([str(id) for id in ankihub_nids]))
        result = {
            uuid.UUID(note.ankihub_note_id): NoteId(note.anki_note_id)
            for note in ah_nid_for_anki_nid
        }
        not_existing = set(ankihub_nids) - set(result.keys())
        return result | dict.fromkeys(not_existing)

    def anki_nid_for_ankihub_nid(self, ankihub_id: uuid.UUID) -> Optional[NoteId]:
        note = (
            AnkiHubNote.select(AnkiHubNote.anki_note_id)
            .where(AnkiHubNote.ankihub_note_id == ankihub_id)
            .get_or_none()
        )

        return NoteId(note.anki_note_id) if note else None

    def remove_deck(self, ankihub_did: uuid.UUID):
        """Removes all data for the given deck from the AnkiHub DB"""
        did = str(ankihub_did)
        AnkiHubNote.delete().where(AnkiHubNote.ankihub_deck_id == did).execute()
        AnkiHubNoteType.delete().where(AnkiHubNoteType.ankihub_deck_id == did).execute()
        DeckMedia.delete().where(DeckMedia.ankihub_deck_id == did).execute()

    def ankihub_deck_ids(self) -> List[uuid.UUID]:
        return [
            uuid.UUID(note.ankihub_deck_id)
            for note in AnkiHubNote.select(AnkiHubNote.ankihub_deck_id).distinct()
        ]

    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        return (
            AnkiHubNote.select(AnkiHubNote.mod)
            .where(AnkiHubNote.ankihub_note_id == str(ankihub_note_id))
            .scalar()
        )

    def ankihub_dids_of_decks_with_missing_values(self) -> List[uuid.UUID]:
        # currently only checks the guid, fields and tags columns
        query = (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .distinct()
            .where(
                (AnkiHubNote.guid.is_null(True))
                | (AnkiHubNote.fields.is_null(True))
                | (AnkiHubNote.tags.is_null(True))
            )
        )
        return [note.ankihub_deck_id for note in query]

    # Media related functions
    def upsert_deck_media_infos(
        self,
        ankihub_did: uuid.UUID,
        media_list: List[DeckMediaClientModel],
    ) -> None:
        """Upsert deck media to the AnkiHub DB."""
        for media_file in media_list:
            (
                DeckMedia.insert(
                    name=media_file.name,
                    ankihub_deck_id=str(ankihub_did),
                    file_content_hash=media_file.file_content_hash,
                    modified=media_file.modified,
                    referenced_on_accepted_note=media_file.referenced_on_accepted_note,
                    exists_on_s3=media_file.exists_on_s3,
                    download_enabled=media_file.download_enabled,
                )
                .on_conflict_replace()
                .execute()
            )

    def downloadable_media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which can be downloaded for the given deck."""
        query = DeckMedia.select(DeckMedia.name).where(
            DeckMedia.ankihub_deck_id == str(ah_did),
            DeckMedia.referenced_on_accepted_note,
            DeckMedia.exists_on_s3,
            DeckMedia.download_enabled,
        )

        return {media.name for media in query}

    def media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which are referenced on notes in the given deck."""
        notes = AnkiHubNote.select(AnkiHubNote.fields).where(
            AnkiHubNote.ankihub_deck_id == str(ah_did),
            (
                AnkiHubNote.fields.contains("<img")
                | AnkiHubNote.fields.contains("[sound:")
            ),
        )
        return {
            media_name
            for note in notes
            for media_name in local_media_names_from_html(note.fields)
        }

    def media_names_exist_for_ankihub_deck(
        self, ah_did: uuid.UUID, media_names: Set[str]
    ) -> Dict[str, bool]:
        """Returns a dictionary where each key is a media name and the corresponding value is a boolean
        indicating whether the media file is referenced on a note in the given deck.
        The media file doesn't have to exist on S3, it just has to referenced on a note in the deck.
        """
        query = DeckMedia.select(DeckMedia.name).where(
            DeckMedia.ankihub_deck_id == str(ah_did),
            DeckMedia.name.in_(media_names),
            DeckMedia.referenced_on_accepted_note,
        )
        names_in_db = {media.name for media in query}
        return {name: (name in names_in_db) for name in media_names}

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

        query = DeckMedia.select(DeckMedia.file_content_hash, DeckMedia.name).where(
            DeckMedia.ankihub_deck_id == str(ah_did),
            DeckMedia.file_content_hash.in_(list(media_to_hash.values())),
        )

        hash_to_media = {media.file_content_hash: media.name for media in query}

        return {
            media_name: matching_media
            for media_name, media_hash in media_to_hash.items()
            if (matching_media := hash_to_media.get(media_hash)) is not None
        }

    # note types
    def upsert_note_type(self, ankihub_did: uuid.UUID, note_type: NotetypeDict) -> None:
        (
            AnkiHubNoteType.insert(
                anki_note_type_id=note_type["id"],
                ankihub_deck_id=str(ankihub_did),
                name=note_type["name"],
                note_type_dict_json=json.dumps(note_type),
            )
            .on_conflict_replace()
            .execute()
        )

    def note_type_dict(
        self, ankihub_did: uuid.UUID, note_type_id: NotetypeId
    ) -> NotetypeDict:
        query = (
            AnkiHubNoteType.select(AnkiHubNoteType.note_type_dict_json)
            .where(
                AnkiHubNoteType.anki_note_type_id == note_type_id,
                AnkiHubNoteType.ankihub_deck_id == str(ankihub_did),
            )
            .get_or_none()
        )

        if query is None:
            return None

        note_type_dict_json = query.note_type_dict_json
        return NotetypeDict(json.loads(note_type_dict_json))

    def ankihub_note_type_ids(self) -> List[NotetypeId]:
        return [
            note_type.anki_note_type_id
            for note_type in AnkiHubNoteType.select(AnkiHubNoteType.anki_note_type_id)
        ]

    def is_ankihub_note_type(self, anki_note_type_id: NotetypeId) -> bool:
        return (
            AnkiHubNoteType.select()
            .where(AnkiHubNoteType.anki_note_type_id == anki_note_type_id)
            .exists()
        )

    def note_types_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NotetypeId]:
        query = AnkiHubNoteType.select(AnkiHubNoteType.anki_note_type_id).where(
            AnkiHubNoteType.ankihub_deck_id == str(ankihub_did)
        )

        return [note_type.anki_note_type_id for note_type in query]

    def ankihub_dids_for_note_type(
        self, anki_note_type_id: NotetypeId
    ) -> Optional[Set[uuid.UUID]]:
        """Returns the AnkiHub deck ids that use the given note type."""
        query = AnkiHubNoteType.select(AnkiHubNoteType.ankihub_deck_id).where(
            AnkiHubNoteType.anki_note_type_id == anki_note_type_id
        )

        did_strings = [note_type.ankihub_deck_id for note_type in query]

        if not did_strings:
            return None

        return {uuid.UUID(did_str) for did_str in did_strings}

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
