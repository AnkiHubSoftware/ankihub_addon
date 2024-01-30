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
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.models import NotetypeDict, NotetypeId
from anki.notes import NoteId
from anki.utils import join_fields, split_fields
from peewee import DQ

from .. import LOGGER
from ..ankihub_client import Field, NoteInfo, suggestion_type_from_str
from ..ankihub_client.models import DeckMedia as DeckMediaClientModel
from ..common_utils import local_media_names_from_html
from .exceptions import IntegrityError
from .models import (
    AnkiHubNote,
    AnkiHubNoteType,
    DeckMedia,
    bind_peewee_models,
    create_tables,
    get_peewee_database,
    set_peewee_database,
)


class _AnkiHubDB:
    database_path: Optional[Path] = None

    def setup_and_migrate(self, db_path: Path) -> None:
        self.database_path = db_path

        set_peewee_database(db_path)

        journal_mode = get_peewee_database().pragma("journal_mode", "wal")
        if journal_mode != "wal":
            LOGGER.warning("Failed to set journal_mode=wal")  # pragma: no cover

        if self.schema_version() == 0:
            bind_peewee_models()
            create_tables()
            get_peewee_database().pragma("user_version", 11)
        else:
            from .db_migrations import migrate_ankihub_db

            migrate_ankihub_db()
            bind_peewee_models()

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
                conflicting_note_exists = AnkiHubNote.filter(
                    anki_note_id=note_data.anki_nid,
                    ankihub_note_id__ne=note_data.ah_nid,
                ).exists()

                if conflicting_note_exists:
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
                        ankihub_note_id=note_data.ah_nid,
                        ankihub_deck_id=ankihub_did,
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

    def remove_notes(self, ah_nids: List[uuid.UUID]) -> None:
        """Removes notes from the AnkiHub DB"""
        AnkiHubNote.delete().where(AnkiHubNote.ankihub_note_id.in_(ah_nids)).execute()

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
                    AnkiHubNote.ankihub_note_id == note_data.ah_nid
                ).execute()

    def reset_mod_values_in_anki_db(self, anki_nids: List[NoteId]) -> None:
        # resets the mod values of the notes in the Anki DB to the
        # mod values stored in the AnkiHub DB
        nid_mod_tuples = (
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id, AnkiHubNote.mod)
            .filter(anki_note_id__in=anki_nids)
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
        return AnkiHubNote.filter(ankihub_note_id=ankihub_nid).exists()

    def note_data(self, anki_note_id: int) -> Optional[NoteInfo]:
        note = AnkiHubNote.filter(anki_note_id=anki_note_id).get_or_none()

        if not note:
            return None

        field_names = self._note_type_field_names(
            ankihub_did=note.ankihub_deck_id, anki_note_type_id=note.anki_note_type_id
        )

        return NoteInfo(
            ah_nid=note.ankihub_note_id,
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
        return (
            AnkiHubNote.select(AnkiHubNote.anki_note_id)
            .filter(ankihub_deck_id=ankihub_did)
            .objects(flat)
        )

    def ankihub_dids(self) -> List[uuid.UUID]:
        return AnkiHubNote.select(AnkiHubNote.ankihub_deck_id).distinct().objects(flat)

    def ankihub_did_for_anki_nid(self, anki_nid: NoteId) -> Optional[uuid.UUID]:
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .filter(anki_note_id=anki_nid)
            .scalar()
        )

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_deck_id)
            .filter(AnkiHubNote.anki_note_id.in_(anki_nids))
            .distinct()
            .objects(flat)
        )

    def anki_nid_to_ah_did_dict(
        self, anki_nids: Iterable[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        """Returns a dict mapping anki nids to the ankihub did of the deck the note is in.
        Not found nids are omitted from the dict."""
        return dict(
            AnkiHubNote.select(AnkiHubNote.anki_note_id, AnkiHubNote.ankihub_deck_id)
            .filter(anki_note_id__in=anki_nids)
            .tuples()
        )

    def are_ankihub_notes(self, anki_nids: List[NoteId]) -> bool:
        notes_count = AnkiHubNote.filter(anki_note_id__in=anki_nids).count()
        return notes_count == len(set(anki_nids))

    def ankihub_nid_for_anki_nid(self, anki_note_id: NoteId) -> Optional[uuid.UUID]:
        return (
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id)
            .filter(anki_note_id=anki_note_id)
            .scalar()
        )

    def anki_nids_to_ankihub_nids(
        self, anki_nids: List[NoteId]
    ) -> Dict[NoteId, uuid.UUID]:
        anki_nid_to_ah_nid = dict(
            AnkiHubNote.select(AnkiHubNote.anki_note_id, AnkiHubNote.ankihub_note_id)
            .filter(anki_note_id__in=anki_nids)
            .tuples()
        )

        not_existing = set(anki_nids) - set(anki_nid_to_ah_nid.keys())
        return anki_nid_to_ah_nid | dict.fromkeys(not_existing)

    def ankihub_nids_to_anki_nids(
        self, ankihub_nids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, NoteId]:
        ah_nid_to_anki_nid = dict(
            AnkiHubNote.select(AnkiHubNote.ankihub_note_id, AnkiHubNote.anki_note_id)
            .filter(ankihub_note_id__in=[str(id) for id in ankihub_nids])
            .tuples()
        )

        not_existing = set(ankihub_nids) - set(ah_nid_to_anki_nid.keys())
        return ah_nid_to_anki_nid | dict.fromkeys(not_existing)

    def anki_nid_for_ankihub_nid(self, ankihub_id: uuid.UUID) -> Optional[NoteId]:
        return (
            AnkiHubNote.select(AnkiHubNote.anki_note_id)
            .filter(ankihub_note_id=ankihub_id)
            .scalar()
        )

    def remove_deck(self, ankihub_did: uuid.UUID):
        """Removes all data for the given deck from the AnkiHub DB"""
        AnkiHubNote.delete().where(AnkiHubNote.ankihub_deck_id == ankihub_did).execute()
        AnkiHubNoteType.delete().where(
            AnkiHubNoteType.ankihub_deck_id == ankihub_did
        ).execute()
        DeckMedia.delete().where(DeckMedia.ankihub_deck_id == ankihub_did).execute()

    def ankihub_deck_ids(self) -> List[uuid.UUID]:
        return AnkiHubNote.select(AnkiHubNote.ankihub_deck_id).distinct().objects(flat)

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
            .filter(DQ(guid__is=None) | DQ(fields__is=None) | DQ(tags__is=None))
            .objects(flat)
        )

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
                    ankihub_deck_id=ankihub_did,
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
            (DQ(fields__ilike="%<img%") | DQ(fields__ilike="%[sound:%")),
            ankihub_deck_id=ah_did,
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
        names_in_db = set(
            DeckMedia.select(DeckMedia.name)
            .filter(
                ankihub_deck_id=ah_did,
                name__in=media_names,
                referenced_on_accepted_note__is=True,
            )
            .objects(flat)
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
            DeckMedia.select(DeckMedia.file_content_hash, DeckMedia.name)
            .filter(
                ankihub_deck_id=ah_did,
                file_content_hash__in=list(media_to_hash.values()),
            )
            .tuples()
        )

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
                ankihub_deck_id=ankihub_did,
                name=note_type["name"],
                note_type_dict=note_type,
            )
            .on_conflict_replace()
            .execute()
        )

    def note_type_dict(
        self, ankihub_did: uuid.UUID, note_type_id: NotetypeId
    ) -> NotetypeDict:
        return (
            AnkiHubNoteType.select(AnkiHubNoteType.note_type_dict)
            .filter(
                anki_note_type_id=note_type_id,
                ankihub_deck_id=ankihub_did,
            )
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

    def ankihub_dids_for_note_type(
        self, anki_note_type_id: NotetypeId
    ) -> Optional[Set[uuid.UUID]]:
        """Returns the AnkiHub deck ids that use the given note type."""
        result = set(
            AnkiHubNoteType.select(AnkiHubNoteType.ankihub_deck_id)
            .filter(anki_note_type_id=anki_note_type_id)
            .objects(flat)
        )
        return result if result else None

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


def flat(**row_data: Dict[str, Any]) -> Any:
    """Return the value from a single-item dictionary."""
    [(_, field_value)] = row_data.items()
    return field_value
