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

import logging
import time
import uuid
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    cast,
)

import aqt
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from anki.utils import ids2str
from peewee import DQ, SqliteDatabase

from ..ankihub_client import Field, NoteInfo, suggestion_type_from_str
from ..ankihub_client.models import DeckMedia as DeckMediaClientModel
from ..ankihub_client.models import SuggestionType
from ..common_utils import local_media_names_from_html
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .exceptions import IntegrityError, MissingValueError
from .models import (
    AnkiHubNote,
    AnkiHubNoteType,
    DeckMedia,
    bind_peewee_models,
    create_tables,
    get_peewee_database,
    set_peewee_database,
)
from .utils import TimedLock

# Change the log level of the peewee logger to not show debug messges which show the sql queries.
# These messages are bad for performance when e.g. inserting a lot entries into the database.
peewee_logger = logging.getLogger("peewee")
peewee_logger.setLevel(logging.INFO)

# Chunk size for executing queries in chunks to avoid SQLite's "too many SQL variables" error.
# The variable limit is 32_766, so the chunk size is set to 30_000 to be safe.
DEFAULT_CHUNK_SIZE = 30_000

# Timeout duration for the write lock. We use a timeout to make sure that deadlocks don't occur.
WRITE_LOCK_TIMEOUT_SECONDS = 10

NOTE_NOT_DELETED_CONDITION = DQ(last_update_type__is=None) | DQ(
    last_update_type__ne=SuggestionType.DELETE.value[0]
)


class _AnkiHubDB:
    database_path: Optional[Path] = None

    # Lock for write operations to the AnkiHub DB. This is used to prevent concurrent write operations
    # which can lead to "OperationalError: database is locked" errors
    write_lock = TimedLock(timeout_seconds=WRITE_LOCK_TIMEOUT_SECONDS)

    def setup_and_migrate(self, db_path: Path) -> None:
        self.database_path = db_path

        set_peewee_database(db_path)

        if self.schema_version() == 0:
            bind_peewee_models()
            create_tables()
            get_peewee_database().pragma("user_version", 13)
        else:
            from .db_migrations import migrate_ankihub_db

            migrate_ankihub_db()
            bind_peewee_models()

    @property
    def db(self) -> SqliteDatabase:
        return get_peewee_database()

    def schema_version(self) -> int:
        return get_peewee_database().pragma("user_version")

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
            2. Call update_mod_values_based_on_anki_db to update the mod values of the upserted notes.
        """
        # Check if all note types used by notes exist in the AnkiHub DB before inserting
        mids_of_notes = set(note_data.mid for note_data in notes_data)
        mids_in_db = set(self.note_types_for_ankihub_deck(ankihub_did))
        missing_mids = [mid for mid in mids_of_notes if mid not in mids_in_db]
        if missing_mids:
            raise IntegrityError(
                "Can't insert notes data because the following note types are "
                f"missing from the AnkiHub DB: {missing_mids}"
            )

        skipped_notes = self._determine_notes_to_skip(
            notes_data, ankihub_did=ankihub_did
        )
        nids_to_skip = set(note_data.anki_nid for note_data in skipped_notes)
        notes_data = [
            note_data
            for note_data in notes_data
            if note_data.anki_nid not in nids_to_skip
        ]

        upserted_notes: List[NoteInfo] = []
        note_dicts = []
        for note_data in notes_data:
            # Prepare fields and tags for insertion
            fields_dict = {
                field.name: field.value
                for field in note_data.fields
                if field.value and field.name != ANKIHUB_NOTE_TYPE_FIELD_NAME
            }
            tags = " ".join([tag for tag in note_data.tags if tag is not None])
            note_dicts.append(
                {
                    "ankihub_note_id": note_data.ah_nid,
                    "ankihub_deck_id": ankihub_did,
                    "anki_note_id": note_data.anki_nid,
                    "anki_note_type_id": note_data.mid,
                    "fields": fields_dict,
                    "tags": tags,
                    "guid": note_data.guid,
                    "last_update_type": (
                        note_data.last_update_type.value[0]
                        if note_data.last_update_type is not None
                        else None
                    ),
                }
            )
            upserted_notes.append(note_data)

        # The chunk size is chosen as 1/10 of the default chunk size, because we need < 10 SQL variables
        # for each deck media entry. The purpose is to avoid the "too many SQL variables" error.
        with self.write_lock, self.db.atomic():
            for chunk in chunks(note_dicts, int(DEFAULT_CHUNK_SIZE / 10)):
                AnkiHubNote.insert_many(chunk).on_conflict_replace().execute()

        return tuple(upserted_notes), tuple(skipped_notes)

    def _determine_notes_to_skip(
        self, notes_data: List[NoteInfo], ankihub_did: uuid.UUID
    ) -> List[NoteInfo]:
        """
        Determines which notes data to skip when upserting notes data into the AnkiHub DB.

        This function checks for each note if a note with the same Anki nid,
        but different AnkiHub deck id exists in the AnkiHub DB and is not marked as
        deleted. Notes that meet these conditions are considered conflicting
        and are added to the list of notes to skip.

        Deleted notes are not considered conflicting, this way their entries in
        the DB can be overwritten. This prevents the situation where a deleted
        note is blocking the insertion of a new note with the same Anki nid
        from another deck.

        Args:
            notes_data (List[NoteInfo]): A list of NoteInfo objects representing the notes to check.
            ankihub_did (uuid.UUID): The AnkiHub deck id the notes are being inserted into.

        Returns:
            List[NoteInfo]: A list of NoteInfo objects representing the notes to skip.
        """

        anki_nids = [note_data.anki_nid for note_data in notes_data]
        conflicting_anki_nids = set(
            execute_list_query_in_chunks(
                lambda anki_nids: AnkiHubNote.select(AnkiHubNote.anki_note_id)
                .filter(
                    NOTE_NOT_DELETED_CONDITION,
                    ankihub_deck_id__ne=ankihub_did,
                    anki_note_id__in=anki_nids,
                )
                .objects(flat),
                anki_nids,
            )
        )

        return [
            note_data
            for note_data in notes_data
            if note_data.anki_nid in conflicting_anki_nids
        ]

    def remove_notes(self, ah_nids: List[uuid.UUID]) -> None:
        """Removes notes from the AnkiHub DB"""
        with self.write_lock, self.db.atomic():
            execute_modifying_query_in_chunks(
                lambda ah_nids: (
                    AnkiHubNote.delete()
                    .where(AnkiHubNote.ankihub_note_id.in_(ah_nids))
                    .execute(),
                ),
                ids=ah_nids,
            )

    def update_mod_values_based_on_anki_db(
        self, notes_data: Sequence[NoteInfo]
    ) -> None:
        """Updates the 'mod' values of notes in the AnkiHub database based on
        their corresponding values in the Anki database.

        This function should be called after importing or exporting notes, once
        the 'mod' values in the Anki database have been updated. The 'mod'
        values are used to determine if a note has been modified in Anki since
        it was last imported/exported. If a note does not exist in the Anki
        database, its 'mod' value is set to the current time.
        """
        anki_nids = [note_data.anki_nid for note_data in notes_data]
        nid_mod_tuples = aqt.mw.col.db.all(
            f"SELECT id, mod FROM notes WHERE id IN {ids2str(anki_nids)}"
        )
        nid_to_mod_dict = {nid: mod for nid, mod in nid_mod_tuples}

        notes = []
        for note_data in notes_data:
            mod = nid_to_mod_dict.get(note_data.anki_nid)
            if not mod:
                # The format of mod is seconds since the epoch.
                mod = int(time.time())
            note = AnkiHubNote(ankihub_note_id=note_data.ah_nid, mod=mod)
            notes.append(note)

        with self.write_lock, self.db.atomic():
            # The chunk size is chosen as 1/10 of the default chunk size, because we need < 10 SQL variables
            # for each entry. The purpose is to avoid the "too many SQL variables" error.
            AnkiHubNote.bulk_update(
                notes, fields=[AnkiHubNote.mod], batch_size=int(DEFAULT_CHUNK_SIZE / 10)
            )

    def reset_mod_values_in_anki_db(self, anki_nids: List[NoteId]) -> None:
        # resets the mod values of the notes in the Anki DB to the
        # mod values stored in the AnkiHub DB
        nid_mod_tuples = execute_list_query_in_chunks(
            lambda anki_nids: (
                AnkiHubNote.select(AnkiHubNote.ankihub_note_id, AnkiHubNote.mod)
                .filter(anki_note_id__in=anki_nids)
                .tuples()
            ),
            ids=anki_nids,
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
        return AnkiHubNote.filter(
            NOTE_NOT_DELETED_CONDITION, ankihub_note_id=ankihub_nid
        ).exists()

    def note_data(self, anki_note_id: int) -> Optional[NoteInfo]:
        note = AnkiHubNote.filter(
            NOTE_NOT_DELETED_CONDITION, anki_note_id=anki_note_id
        ).get_or_none()

        if not note:
            return None

        field_names = self.note_type_field_names(
            anki_note_type_id=note.anki_note_type_id
        )

        return self._build_note_info(note, {note.anki_note_type_id: field_names})

    def notes_data_for_anki_nids(self, anki_nids: Sequence[NoteId]) -> List[NoteInfo]:
        notes = execute_list_query_in_chunks(
            lambda anki_nids: (
                AnkiHubNote.select().filter(
                    NOTE_NOT_DELETED_CONDITION,
                    anki_note_id__in=anki_nids,
                )
            ),
            ids=list(anki_nids),
        )

        field_names_by_mid: Dict[NotetypeId, List[str]] = {}
        for note in notes:
            if note.anki_note_type_id not in field_names_by_mid:
                field_names_by_mid[note.anki_note_type_id] = self.note_type_field_names(
                    anki_note_type_id=cast(NotetypeId, note.anki_note_type_id),
                )

        return [self._build_note_info(note, field_names_by_mid) for note in notes]

    def _build_note_info(
        self, note: AnkiHubNote, field_names_by_mid: Dict[NotetypeId, List[str]]
    ) -> NoteInfo:
        if note.fields is None:
            raise MissingValueError(ah_did=cast(uuid.UUID, note.ankihub_deck_id))

        return NoteInfo(
            ah_nid=cast(uuid.UUID, note.ankihub_note_id),
            anki_nid=cast(int, note.anki_note_id),
            mid=cast(int, note.anki_note_type_id),
            tags=aqt.mw.col.tags.split(cast(str, note.tags)),
            fields=[
                Field(
                    name=field_name,
                    value=note.fields.get(field_name, ""),  # type: ignore
                )
                for field_name in field_names_by_mid[
                    cast(NotetypeId, note.anki_note_type_id)
                ]
                if field_name != ANKIHUB_NOTE_TYPE_FIELD_NAME
            ],
            guid=cast(str, note.guid),
            last_update_type=(
                suggestion_type_from_str(cast(str, note.last_update_type))
                if note.last_update_type
                else None
            ),
        )

    def anki_nids_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NoteId]:
        return (
            AnkiHubNote.select(AnkiHubNote.anki_note_id)
            .filter(NOTE_NOT_DELETED_CONDITION, ankihub_deck_id=ankihub_did)
            .objects(flat)
        )

    def ankihub_dids(self) -> List[uuid.UUID]:
        return AnkiHubNote.select(AnkiHubNote.ankihub_deck_id).distinct().objects(flat)

    def ankihub_did_for_anki_nid(self, anki_nid: NoteId) -> Optional[uuid.UUID]:
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .filter(NOTE_NOT_DELETED_CONDITION, anki_note_id=anki_nid)
            .scalar()
        )

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        return execute_list_query_in_chunks(
            lambda anki_nids: (
                AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
                .filter(NOTE_NOT_DELETED_CONDITION, anki_note_id__in=anki_nids)
                .distinct()
                .objects(flat)
            ),
            ids=list(anki_nids),
        )

    def anki_nid_to_ah_did_dict(
        self, anki_nids: Iterable[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        """Returns a dict mapping anki nids to the ankihub did of the deck the note is in.
        Not found nids are omitted from the dict."""
        return dict(
            execute_list_query_in_chunks(
                lambda anki_nids: (
                    AnkiHubNote.select(
                        AnkiHubNote.anki_note_id, AnkiHubNote.ankihub_deck_id
                    )
                    .filter(NOTE_NOT_DELETED_CONDITION, anki_note_id__in=anki_nids)
                    .tuples()
                ),
                ids=list(anki_nids),
            )
        )

    def ankihub_nid_for_anki_nid(self, anki_note_id: NoteId) -> Optional[uuid.UUID]:
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id)
            .filter(NOTE_NOT_DELETED_CONDITION, anki_note_id=anki_note_id)
            .scalar()
        )

    def ankihub_nids_to_anki_nids(
        self, ankihub_nids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, NoteId]:
        ah_nid_to_anki_nid = dict(
            execute_list_query_in_chunks(
                lambda ankihub_nids: (
                    AnkiHubNote.select(
                        AnkiHubNote.ankihub_note_id, AnkiHubNote.anki_note_id
                    )
                    .filter(
                        NOTE_NOT_DELETED_CONDITION,
                        ankihub_note_id__in=ankihub_nids,
                    )
                    .tuples()
                ),
                ids=ankihub_nids,
            )
        )

        not_existing = set(ankihub_nids) - set(ah_nid_to_anki_nid.keys())
        return ah_nid_to_anki_nid | dict.fromkeys(not_existing)

    def anki_nid_for_ankihub_nid(self, ankihub_id: uuid.UUID) -> Optional[NoteId]:
        return (
            AnkiHubNote.select(AnkiHubNote.anki_note_id)
            .filter(NOTE_NOT_DELETED_CONDITION, ankihub_note_id=ankihub_id)
            .scalar()
        )

    def remove_deck(self, ankihub_did: uuid.UUID):
        """Removes all data for the given deck from the AnkiHub DB"""
        with self.write_lock, self.db.atomic():
            AnkiHubNote.delete().where(
                AnkiHubNote.ankihub_deck_id == ankihub_did
            ).execute()
            self.remove_note_types_of_deck(ankihub_did)
            DeckMedia.delete().where(DeckMedia.ankihub_deck_id == ankihub_did).execute()

    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        return (
            AnkiHubNote.select(AnkiHubNote.mod)
            .filter(ankihub_note_id=ankihub_note_id)
            .scalar()
        )

    def ankihub_dids_of_decks_with_missing_values(self) -> List[uuid.UUID]:
        # currently only checks the guid, fields and tags columns
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .distinct()
            .filter(
                NOTE_NOT_DELETED_CONDITION,
                DQ(guid__is=None) | DQ(fields__is=None) | DQ(tags__is=None),
            )
            .objects(flat)
        )

    # Media related functions
    def upsert_deck_media_infos(
        self,
        ankihub_did: uuid.UUID,
        media_list: List[DeckMediaClientModel],
    ) -> None:
        """Upsert deck media to the AnkiHub DB."""
        deck_media_dicts = [
            {
                "name": deck_media.name,
                "ankihub_deck_id": ankihub_did,
                "file_content_hash": deck_media.file_content_hash,
                "modified": deck_media.modified,
                "referenced_on_accepted_note": deck_media.referenced_on_accepted_note,
                "exists_on_s3": deck_media.exists_on_s3,
                "download_enabled": deck_media.download_enabled,
            }
            for deck_media in media_list
        ]

        with self.write_lock, self.db.atomic():
            # The chunk size is chosen as 1/10 of the default chunk size, because we need < 10 SQL variables
            # for each deck media entry. The purpose is to avoid the "too many SQL variables" error.
            for chunk in chunks(deck_media_dicts, int(DEFAULT_CHUNK_SIZE / 10)):
                DeckMedia.insert_many(chunk).on_conflict_replace().execute()

    def downloadable_media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which can be downloaded for the given deck."""
        return set(
            DeckMedia.select(DeckMedia.name)
            .filter(
                ankihub_deck_id=ah_did,
                referenced_on_accepted_note__is=True,
                exists_on_s3__is=True,
                download_enabled__is=True,
            )
            .objects(flat)
        )

    def media_names_for_ankihub_deck(self, ah_did: uuid.UUID) -> Set[str]:
        """Returns the names of all media files which are referenced on notes in the given deck."""
        notes = AnkiHubNote.select(AnkiHubNote.fields).filter(
            NOTE_NOT_DELETED_CONDITION,
            (
                AnkiHubNote.fields.cast("text").ilike("%<img%")
                | AnkiHubNote.fields.cast("text").ilike("%[sound:%")
            ),
            ankihub_deck_id=ah_did,
        )
        return {
            media_name
            for note in notes
            for field_value in (note.fields.values() if note.fields else [])
            for media_name in local_media_names_from_html(field_value)
        }

    def media_names_exist_for_ankihub_deck(
        self, ah_did: uuid.UUID, media_names: Set[str]
    ) -> Dict[str, bool]:
        """Returns a dictionary where each key is a media name and the corresponding value is a boolean
        indicating whether the media file is referenced on a note in the given deck.
        The media file doesn't have to exist on S3, it just has to referenced on a note in the deck.
        """
        names_in_db = set(
            execute_list_query_in_chunks(
                lambda media_names: (
                    DeckMedia.select(DeckMedia.name)
                    .filter(
                        ankihub_deck_id=ah_did,
                        name__in=media_names,
                        referenced_on_accepted_note__is=True,
                    )
                    .objects(flat)
                ),
                ids=list(media_names),
            )
        )
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

        hash_to_media = dict(
            execute_list_query_in_chunks(
                lambda media_hashes: (
                    DeckMedia.select(DeckMedia.file_content_hash, DeckMedia.name)
                    .filter(
                        ankihub_deck_id=ah_did,
                        file_content_hash__in=media_hashes,
                    )
                    .tuples()
                ),
                ids=list(media_to_hash.values()),
            )
        )

        return {
            media_name: matching_media
            for media_name, media_hash in media_to_hash.items()
            if (matching_media := hash_to_media.get(media_hash)) is not None
        }

    # note types
    def upsert_note_type(self, ankihub_did: uuid.UUID, note_type: NotetypeDict) -> None:
        with self.write_lock:
            (
                AnkiHubNoteType.insert(
                    anki_note_type_id=note_type["id"],
                    ankihub_deck_id=ankihub_did,
                    name=note_type["name"],
                    note_type_dict=note_type,
                )
                .on_conflict_replace()
                .execute()
            )

    def remove_note_types_of_deck(self, ankihub_did: uuid.UUID) -> None:
        with self.write_lock:
            AnkiHubNoteType.delete().where(
                AnkiHubNoteType.ankihub_deck_id == ankihub_did
            ).execute()

    def note_type_dict(self, note_type_id: NotetypeId) -> NotetypeDict:
        return (
            AnkiHubNoteType.select(AnkiHubNoteType.note_type_dict)
            .filter(
                anki_note_type_id=note_type_id,
            )
            .scalar()
        )

    def note_type_id_by_name(self, name: str) -> Optional[NotetypeId]:
        return (
            AnkiHubNoteType.select(AnkiHubNoteType.anki_note_type_id)
            .filter(AnkiHubNoteType.name == name)
            .scalar()
        )

    def ankihub_note_type_ids(self) -> List[NotetypeId]:
        return AnkiHubNoteType.select(AnkiHubNoteType.anki_note_type_id).objects(flat)

    def is_ankihub_note_type(self, anki_note_type_id: NotetypeId) -> bool:
        return (
            AnkiHubNoteType.select()
            .filter(anki_note_type_id=anki_note_type_id)
            .exists()
        )

    def note_types_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NotetypeId]:
        return (
            AnkiHubNoteType.select(AnkiHubNoteType.anki_note_type_id)
            .filter(ankihub_deck_id=ankihub_did)
            .objects(flat)
        )

    def note_type_names_and_ids_for_ankihub_deck(
        self, ankihub_did: uuid.UUID
    ) -> List[Tuple[str, NotetypeId]]:
        return (
            AnkiHubNoteType.select(
                AnkiHubNoteType.name, AnkiHubNoteType.anki_note_type_id
            )
            .filter(ankihub_deck_id=ankihub_did)
            .objects(lambda name, anki_note_type_id: (name, anki_note_type_id))
        )

    def ankihub_did_for_note_type(self, anki_note_type_id: NotetypeId) -> uuid.UUID:
        return (
            AnkiHubNoteType.select(AnkiHubNoteType.ankihub_deck_id)
            .filter(anki_note_type_id=anki_note_type_id)
            .scalar()
        )

    def note_type_field_names(self, anki_note_type_id: NotetypeId) -> List[str]:
        """Returns the names of the fields of the note type."""
        result = [
            field["name"]
            for field in sorted(
                (field for field in self.note_type_dict(anki_note_type_id)["flds"]),
                key=lambda f: f["ord"],
            )
        ]
        return result


ankihub_db = _AnkiHubDB()


def flat(**row_data: Dict[str, Any]) -> Any:
    """Return the value from a single-item dictionary."""
    [(_, field_value)] = row_data.items()
    return field_value


Id = TypeVar("Id")
ResultEntry = TypeVar("ResultEntry")


def execute_count_query_in_chunks(
    query_func: Callable[[Sequence[Id]], int],
    ids: List[Id],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> int:
    """Execute a count query function in chunks to avoid SQLite's "too many SQL variables" error.
    The query function should take a list of ids and return a count.
    """
    return execute_query_in_chunks(
        query_func,
        ids,
        accumulator=lambda total, count: total + count,
        initial=0,
        chunk_size=chunk_size,
    )


def execute_list_query_in_chunks(
    query_func: Callable[[Sequence[Id]], List[ResultEntry]],
    ids: List[Id],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> List[ResultEntry]:
    """Execute a list query function in chunks to avoid SQLite's "too many SQL variables" error.
    The query function should take a list of ids and return a list of results.
    """
    return execute_query_in_chunks(
        query_func,
        ids,
        accumulator=lambda total, chunk: total + list(chunk),
        initial=[],
        chunk_size=chunk_size,
    )


def execute_query_in_chunks(
    query_func: Callable[[Sequence[Id]], ResultEntry],
    ids: List[Id],
    accumulator: Callable[[ResultEntry, ResultEntry], ResultEntry],
    initial: ResultEntry,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ResultEntry:
    """Execute a query function in chunks to avoid SQLite's "too many SQL variables" error.
    The query function should take a list of ids and return a result.
    The accumulator function is used to accumulate the results.
    """
    result: ResultEntry = initial
    for ids_chunk in chunks(ids, chunk_size):
        result_chunk = query_func(ids_chunk)
        result = accumulator(result, result_chunk)
    return result


def execute_modifying_query_in_chunks(
    modifying_query_func: Callable[[Sequence[Id]], Any],
    ids: List[Id],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    """Execute a modifying query function in chunks to avoid SQLite's "too many SQL variables" error."""
    for ids_chunk in chunks(ids, chunk_size):
        modifying_query_func(ids_chunk)


T = TypeVar("T")


def chunks(items: List[T], chunk_size: int) -> Generator[List[T], None, None]:
    """Yield chunks of size 'chunk_size' from 'items' list."""
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]
