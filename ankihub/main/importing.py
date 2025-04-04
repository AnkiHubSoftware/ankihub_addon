"""Import NoteInfo objects and note types into Anki and the AnkiHub database,
create/update decks and note types in the Anki collection if necessary"""

import copy
import uuid
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import Collection, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki import consts as anki_consts
from anki.cards import Card
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import ids2str

from .. import LOGGER, settings
from ..ankihub_client import Field, NoteInfo
from ..ankihub_client.models import SuggestionType
from ..db import ankihub_db
from ..settings import (
    TAG_FOR_INSTRUCTION_NOTES,
    BehaviorOnRemoteNoteDeleted,
    SuspendNewCardsOfExistingNotes,
    is_anking_note_types_addon_installed,
    is_projektanki_note_types_addon_installed,
)
from .deck_options import set_ankihub_config_for_deck
from .exceptions import ChangesRequireFullSyncError
from .note_conversion import (
    TAG_FOR_PROTECTING_ALL_FIELDS,
    get_fields_protected_by_tags,
    is_internal_tag,
    is_optional_tag,
)
from .note_deletion import TAG_FOR_DELETED_NOTES
from .subdecks import build_subdecks_and_move_cards_to_them
from .utils import (
    add_notes,
    change_note_types_of_notes,
    create_deck_with_id,
    create_note_type_with_id,
    dids_of_notes,
    get_unique_ankihub_deck_name,
    is_tag_in_list,
    lowest_level_common_ancestor_did,
    note_type_with_updated_templates_and_css,
    truncated_list,
)


class NoteOperation(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NO_CHANGES = "no changes"


@dataclass(frozen=True)
class AnkiHubImportResult:
    ankihub_did: uuid.UUID
    anki_did: DeckId
    updated_nids: List[NoteId]
    created_nids: List[NoteId]
    deleted_nids: List[NoteId]
    marked_as_deleted_nids: List[NoteId]
    skipped_nids: List[NoteId]
    first_import_of_deck: bool

    def __repr__(self):
        return pformat(self.__dict__)


class AnkiHubImporter:
    def __init__(self):
        self._created_nids: List[NoteId] = []
        self._updated_nids: List[NoteId] = []
        self._deleted_nids: List[NoteId] = []
        self._marked_as_deleted_nids: List[NoteId] = []
        self._skipped_nids: List[NoteId] = []

        self._ankihub_did: Optional[uuid.UUID] = None
        self._is_first_import_of_deck: Optional[bool] = None
        self._protected_fields: Optional[Dict[int, List[str]]] = None
        self._protected_tags: Optional[List[str]] = None
        self._local_did: Optional[DeckId] = None

    def import_ankihub_deck(
        self,
        ankihub_did: uuid.UUID,
        notes: List[NoteInfo],
        note_types: Dict[NotetypeId, NotetypeDict],
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        is_first_import_of_deck: bool,
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
        anki_did: Optional[  # did that new notes should be put into if importing not for the first time
            DeckId
        ] = None,
        subdecks: bool = False,
        subdecks_for_new_notes_only: bool = False,
        recommended_deck_settings: bool = True,
        raise_if_full_sync_required: bool = True,
        clear_ah_note_types_before_import: bool = False,
    ) -> AnkiHubImportResult:
        """
        Used for importing an AnkiHub deck for the first time or for updating it.
        note_types are added to the AnkiHub db and used to create or update note types in the Anki collection if
        necessary. Note types won't be updated to exactly match the provided note types,
        but they will be updated to e.g. have the same fields and field order as the provided note types.
        subdeck indicates whether cards should be moved into subdecks based on subdeck tags
        subdecks_for_new_notes_only indicates whether only new notes should be moved into subdecks
        """
        LOGGER.info(
            "Importing ankihub deck...",
            deck_name=deck_name,
            ankihub_did=ankihub_did,
            anki_did=anki_did,
            is_first_import_of_deck=is_first_import_of_deck,
            notes_count=len(notes),
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            subdecks=subdecks,
            subdecks_for_new_notes_only=subdecks_for_new_notes_only,
        )

        # Instance attributes are reset here so that the results returned are only for the current deck.
        self._created_nids = []
        self._updated_nids = []
        self._skipped_nids = []

        self._ankihub_did = ankihub_did
        self._is_first_import_of_deck = is_first_import_of_deck
        self._protected_fields = protected_fields
        self._protected_tags = protected_tags
        self._local_did = _adjust_deck(deck_name, anki_did)
        self._raise_if_full_sync_required = raise_if_full_sync_required
        self._clear_note_types_before_import = clear_ah_note_types_before_import

        if self._is_first_import_of_deck:
            # Clean up any left over data for this deck in the ankihub database from previous deck imports.
            ankihub_db.remove_deck(ankihub_did)

        self._import_note_types(note_types=note_types)

        dids = set()
        if notes:
            dids = self._import_notes(
                notes_data=notes,
                behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
                suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
                suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
            )

        if self._is_first_import_of_deck:
            self._local_did = self._cleanup_first_time_deck_import(
                dids, self._local_did
            )
            if recommended_deck_settings:
                set_ankihub_config_for_deck(self._local_did)

        if subdecks or subdecks_for_new_notes_only:
            if subdecks_for_new_notes_only:
                anki_nids = list(self._created_nids)
            else:
                anki_nids = list(self._created_nids + self._updated_nids)

            build_subdecks_and_move_cards_to_them(
                ankihub_did=self._ankihub_did, nids=anki_nids
            )

        result = AnkiHubImportResult(
            ankihub_did=ankihub_did,
            anki_did=self._local_did,
            created_nids=self._created_nids,
            updated_nids=self._updated_nids,
            deleted_nids=self._deleted_nids,
            marked_as_deleted_nids=self._marked_as_deleted_nids,
            skipped_nids=self._skipped_nids,
            first_import_of_deck=self._is_first_import_of_deck,
        )
        aqt.mw.col.save()

        return result

    def _import_note_types(self, note_types: Dict[NotetypeId, NotetypeDict]) -> None:
        self._import_note_types_into_ankihub_db(note_types=note_types)
        self._adjust_note_types_in_anki_db(note_types)

    def _adjust_note_types_in_anki_db(
        self, remote_note_types: Dict[NotetypeId, NotetypeDict]
    ) -> None:
        # can be called when installing a deck for the first time and when synchronizing with AnkiHub

        LOGGER.info("Beginning adjusting note types...")
        _create_missing_note_types(remote_note_types)
        _rename_note_types(remote_note_types)
        self._ensure_local_fields_align_with_remote(remote_note_types)
        self._update_templates_and_css(remote_note_types)

        LOGGER.info("Adjusted note types.")

    def _ensure_local_fields_align_with_remote(
        self, remote_note_types: Dict[NotetypeId, NotetypeDict]
    ) -> None:

        note_types_with_field_conflicts: List[Tuple[NotetypeDict, NotetypeDict]] = []
        for mid, remote_note_type in remote_note_types.items():
            local_note_type = aqt.mw.col.models.get(mid)

            local_field_names = [field["name"] for field in local_note_type["flds"]]
            remote_field_names = [field["name"] for field in remote_note_type["flds"]]
            common_field_names_in_local_order = [
                name for name in local_field_names if name in remote_field_names
            ]
            if (
                common_field_names_in_local_order != remote_field_names
                or local_field_names[-1] != settings.ANKIHUB_NOTE_TYPE_FIELD_NAME
            ):
                missing_fields = [
                    name for name in remote_field_names if name not in local_field_names
                ]
                LOGGER.info(
                    (
                        "Field mismatch: local note type doesn't contain all remote fields in the same order,"
                        "or the last field is not the AnkiHub ID field."
                    ),
                    local_note_type_name=local_note_type["name"],
                    local_fields=local_field_names,
                    remote_fields=remote_field_names,
                    missing_fields=missing_fields if missing_fields else None,
                )
                note_types_with_field_conflicts.append(
                    (local_note_type, remote_note_type)
                )

        if self._raise_if_full_sync_required and note_types_with_field_conflicts:
            affected_note_type_ids = set(
                remote_note_type["id"]
                for _, remote_note_type in note_types_with_field_conflicts
            )
            LOGGER.info(
                "Note type field conflicts require full sync.",
                affected_note_type_ids=affected_note_type_ids,
            )
            raise ChangesRequireFullSyncError(
                affected_note_type_ids=affected_note_type_ids
            )

        for local_note_type, remote_note_type in note_types_with_field_conflicts:
            local_note_type["flds"] = _adjust_fields(
                local_note_type["flds"], remote_note_type["flds"]
            )
            aqt.mw.col.models.update_dict(local_note_type)
            LOGGER.info(
                "Fields after updating the note type",
                fields=[field["name"] for field in local_note_type["flds"]],
            )

    def _update_templates_and_css(
        self, remote_note_types: Dict[NotetypeId, NotetypeDict]
    ) -> None:
        anking_note_types_addon_installed = is_anking_note_types_addon_installed()
        projekt_anki_note_types_addon_installed = (
            is_projektanki_note_types_addon_installed()
        )

        should_use_new_templates_by_mid: Dict[NotetypeId, bool] = {}
        for mid, remote_note_type in remote_note_types.items():
            # We don't use new templates and css of AnKing note types if the AnKing note types addon is installed.
            # The AnKing note types addon will handle updating the templates, while preserving the
            # user's customizations.
            # The same applies to ProjektAnki note types and the ProjektAnki note types addon.
            should_use_new_templates_by_mid[mid] = not (
                (
                    "anking" in remote_note_type["name"].lower()
                    and anking_note_types_addon_installed
                )
                or (
                    "projektanki" in remote_note_type["name"].lower()
                    and projekt_anki_note_types_addon_installed
                )
            )

        if self._raise_if_full_sync_required:
            mids_with_template_count_change = [
                mid
                for mid, remote_note_type in remote_note_types.items()
                if len(aqt.mw.col.models.get(mid)["tmpls"])
                != len(remote_note_type["tmpls"])
                and should_use_new_templates_by_mid[mid]
            ]
            if mids_with_template_count_change:
                LOGGER.info(
                    "Template count changes require full sync.",
                    affected_note_type_ids=mids_with_template_count_change,
                )
                raise ChangesRequireFullSyncError(
                    affected_note_type_ids=set(mids_with_template_count_change)
                )

        for mid, remote_note_type in remote_note_types.items():
            local_note_type = aqt.mw.col.models.get(mid)
            updated_note_type = note_type_with_updated_templates_and_css(
                old_note_type=local_note_type,
                new_note_type=(
                    remote_note_type if should_use_new_templates_by_mid[mid] else None
                ),
            )

            aqt.mw.col.models.update_dict(updated_note_type)

    def _import_note_types_into_ankihub_db(
        self, note_types: Dict[NotetypeId, NotetypeDict]
    ) -> None:

        with ankihub_db.db.atomic():
            if self._clear_note_types_before_import:
                ankihub_db.remove_note_types_of_deck(self._ankihub_did)

            for note_type in note_types.values():
                ankihub_db.upsert_note_type(
                    ankihub_did=self._ankihub_did, note_type=note_type
                )

    def _import_notes(
        self,
        notes_data: List[NoteInfo],
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
    ) -> Set[DeckId]:
        """
        Handles the import of notes into the Anki and AnkiHub databases. This
        includes creating and updating notes, and marking notes as deleted in
        AnkiHub. Depending on the deck settings, notes can be deleted or marked
        as deleted in the Anki database.

        Cards in the Anki database may be suspended based on the provided parameters.

        Returns a set of Anki deck IDs that the created or updated or notes
        without changes belong to.
        """
        # Upsert notes into AnkiHub DB.
        upserted_notes_data, skipped_notes_data = ankihub_db.upsert_notes_data(
            ankihub_did=self._ankihub_did, notes_data=notes_data
        )
        self._skipped_nids = [
            NoteId(note_data.anki_nid) for note_data in skipped_notes_data
        ]
        LOGGER.info(
            "Upserted notes into AnkiHub DB.",
            upserted_notes_count=len(upserted_notes_data),
            skipped_notes_count=len(skipped_notes_data),
        )

        # Upsert notes into Anki DB, delete them or mark them as deleted
        self._reset_note_types_of_notes_based_on_notes_data(upserted_notes_data)

        (
            notes_to_create_by_ah_nid,
            notes_to_update,
            notes_to_delete,
            notes_without_changes,
        ) = self._prepare_notes(notes_data=upserted_notes_data)
        LOGGER.info(
            "Prepared notes for import.",
            notes_to_create_count=len(notes_to_create_by_ah_nid),
            notes_to_update_count=len(notes_to_update),
            notes_to_delete_count=len(notes_to_delete),
            notes_without_changes_count=len(notes_without_changes),
        )

        cards_by_anki_nid_before_import = cards_by_anki_nid_dict(notes_to_update)

        self._update_notes(notes_to_update)
        self._create_notes(notes_to_create_by_ah_nid, notes_data=upserted_notes_data)
        self._delete_notes_or_mark_as_deleted(
            notes_to_delete,
            behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
        )

        # Update the AnkiHubNote.mod values in the AnkiHub DB.
        ankihub_db.update_mod_values_based_on_anki_db(notes_data=upserted_notes_data)

        # Suspend new cards in Anki DB if needed.
        notes = list(notes_to_create_by_ah_nid.values()) + notes_to_update
        self._suspend_cards(
            notes=notes,
            cards_by_anki_nid_before=cards_by_anki_nid_before_import,
            suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
            suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
        )

        aqt.mw.col.save()

        self._log_note_import_summary()

        notes = (
            list(notes_to_create_by_ah_nid.values())
            + notes_to_update
            + notes_without_changes
        )
        dids = dids_of_notes(notes)
        return dids

    def _reset_note_types_of_notes_based_on_notes_data(
        self, notes_data: Sequence[NoteInfo]
    ) -> None:
        """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
        nid_mid_pairs = [
            (NoteId(note_data.anki_nid), NotetypeId(note_data.mid))
            for note_data in notes_data
        ]
        change_note_types_of_notes(
            nid_mid_pairs, raise_if_full_sync_required=self._raise_if_full_sync_required
        )

    def _log_note_import_summary(self) -> None:
        LOGGER.info(
            "Note import summary",
            created_notes_count=len(self._created_nids),
            created_notes_truncated=truncated_list(self._created_nids, limit=3),
            updated_notes_count=len(self._updated_nids),
            updated_notes_truncated=truncated_list(self._updated_nids, limit=3),
            deleted_notes_count=len(self._deleted_nids),
            deleted_notes_truncated=truncated_list(self._deleted_nids, limit=3),
            marked_as_deleted_notes_count=len(self._marked_as_deleted_nids),
            marked_as_deleted_notes_truncated=truncated_list(
                self._marked_as_deleted_nids, limit=3
            ),
            skipped_notes_count=len(self._skipped_nids),
            skipped_notes_list=truncated_list(self._skipped_nids, limit=3),
        )

    def _prepare_notes(
        self, notes_data: Collection[NoteInfo]
    ) -> Tuple[Dict[uuid.UUID, Note], List[Note], List[Note], List[Note]]:
        """Prepare Anki notes for import into Anki DB. Fields and tags are updated according to the
        notes_data. The changes are not committed to the Anki DB yet.
        Returns a tuple of (notes_to_create_by_ah_nid, notes_to_update, notes_to_delete, notes_without_changes).
        """
        notes_to_create_by_ah_nid: Dict[uuid.UUID, Note] = {}
        notes_to_update: List[Note] = []
        notes_to_delete: List[Note] = []
        notes_without_changes: List[Note] = []
        for note_data in notes_data:
            note, operation = self._prepare_note(
                note_data=note_data,
                protected_fields=self._protected_fields,
                protected_tags=self._protected_tags,
            )

            if operation == NoteOperation.CREATE:
                notes_to_create_by_ah_nid[note_data.ah_nid] = note
            elif operation == NoteOperation.UPDATE:
                notes_to_update.append(note)
            elif operation == NoteOperation.DELETE:
                notes_to_delete.append(note)
            elif operation == NoteOperation.NO_CHANGES:
                notes_without_changes.append(note)
            else:
                raise ValueError(
                    f"Unknown value for {str(NoteOperation)}"
                )  # pragma: no cover

        return (
            notes_to_create_by_ah_nid,
            notes_to_update,
            notes_to_delete,
            notes_without_changes,
        )

    def _update_notes(self, notes_to_update: List[Note]) -> None:
        if not notes_to_update:
            return

        aqt.mw.col.update_notes(notes_to_update)
        self._updated_nids = [note.id for note in notes_to_update]

    def _create_notes(
        self,
        notes_to_create_by_ah_nid: Dict[uuid.UUID, Note],
        notes_data: Collection[NoteInfo],
    ) -> None:
        if not notes_to_create_by_ah_nid:
            return

        self._create_notes_inner(
            notes_to_create_by_ah_nid=notes_to_create_by_ah_nid,
            notes_data=notes_data,
        )
        self._created_nids = [note.id for note in notes_to_create_by_ah_nid.values()]

    def _delete_notes_or_mark_as_deleted(
        self,
        notes: Collection[Note],
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
    ) -> None:

        # Exclude notes that don't exist in the Anki database.
        note_ids = set(
            aqt.mw.col.db.list(
                f"SELECT id FROM notes WHERE id IN {ids2str(note.id for note in notes)}"
            )
        )
        notes = [note for note in notes if note.id in note_ids]

        if not notes:
            return

        if (
            behavior_on_remote_note_deleted
            == BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
        ):
            nids = [note.id for note in notes]
            nids_of_notes_with_reviews: Set[NoteId] = set(
                aqt.mw.col.db.list(
                    "SELECT DISTINCT nid FROM cards "
                    f"WHERE nid IN {ids2str(nids)} AND "
                    f"id IN (SELECT DISTINCT cid FROM revlog WHERE type != {anki_consts.REVLOG_RESCHED})"
                )
            )
            notes_with_reviews = set(
                note for note in notes if note.id in nids_of_notes_with_reviews
            )
            notes_without_reviews = set(notes) - notes_with_reviews

            self._mark_notes_as_deleted(notes_with_reviews)
            self._delete_notes(notes_without_reviews)

        elif (
            behavior_on_remote_note_deleted == BehaviorOnRemoteNoteDeleted.NEVER_DELETE
        ):
            self._mark_notes_as_deleted(notes)
        else:
            raise ValueError(  # pragma: no cover
                f"Unknown value for {behavior_on_remote_note_deleted=}"
            )

    def _delete_notes(self, notes: Collection[Note]) -> None:
        """Delete notes from the Anki database. Updates the _deleted_nids attribute."""
        if not notes:
            return

        nids_to_delete = [note.id for note in notes]
        changes = aqt.mw.col.remove_notes(nids_to_delete)
        LOGGER.info("Deleted notes.", deleted_notes_count=changes.count)
        self._deleted_nids = nids_to_delete

    def _mark_notes_as_deleted(self, notes: Collection[Note]) -> None:
        """Add a tag to the notes to mark them as deleted and clear their ankihub_id field.
        Updates the _marked_as_deleted_nids attribute.
        By clearing their ankihub_id field the "View on AnkiHub" button won't be shown on mobile for these notes.
        """
        if not notes:
            return

        for note in notes:
            note.tags = list(set(note.tags) | {TAG_FOR_DELETED_NOTES})
            note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME] = ""
        aqt.mw.col.update_notes(list(notes))

        nids = [note.id for note in notes]
        self._marked_as_deleted_nids = nids

        LOGGER.info("Marked notes as deleted.", marked_as_deleted_notes_count=len(nids))

    def _suspend_cards(
        self,
        notes: Collection[Note],
        cards_by_anki_nid_before: Dict[NoteId, List[Card]],
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
    ) -> None:
        cards_to_suspend: List[Card] = []
        for note in notes:
            cards_to_suspend_for_note = self._cards_to_suspend_for_note(
                note=note,
                cards_before_changes=cards_by_anki_nid_before.get(NoteId(note.id), []),
                suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
                suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
            )
            cards_to_suspend.extend(cards_to_suspend_for_note)

        for card in cards_to_suspend:
            card.queue = QUEUE_TYPE_SUSPENDED

        aqt.mw.col.update_cards(cards_to_suspend)

        LOGGER.info(
            "Suspended cards.",
            suspended_cards_count=len(cards_to_suspend),
        )

    def _create_notes_inner(
        self,
        notes_to_create_by_ah_nid: Dict[uuid.UUID, Note],
        notes_data: Collection[NoteInfo],
    ) -> None:
        """Add notes to the Anki database and sets their ids to the ones from the AnkiHub database."""
        notes_data_to_create = [
            note_data
            for note_data in notes_data
            if note_data.ah_nid in notes_to_create_by_ah_nid
        ]

        add_notes(notes=notes_to_create_by_ah_nid.values(), deck_id=self._local_did)

        # Set the nids in the Anki database to the nids of the notes in the AnkiHub database.
        notes_data_by_ah_nid = {
            note_data.ah_nid: note_data for note_data in notes_data_to_create
        }
        case_conditions = " ".join(
            f"WHEN {note.id} THEN {notes_data_by_ah_nid[ah_nid].anki_nid}"
            for ah_nid, note in notes_to_create_by_ah_nid.items()
        )
        anki_nids = ", ".join(
            str(note.id) for note in notes_to_create_by_ah_nid.values()
        )
        aqt.mw.col.db.execute(
            f"UPDATE notes SET id = CASE id {case_conditions} END WHERE id IN ({anki_nids});"
        )
        aqt.mw.col.db.execute(
            f"UPDATE cards SET nid = CASE nid {case_conditions} END WHERE nid IN ({anki_nids});"
        )

        # Update the note ids of the Note objects.
        for ah_nid, note in notes_to_create_by_ah_nid.items():
            note.id = NoteId(notes_data_by_ah_nid[ah_nid].anki_nid)

    def _cleanup_first_time_deck_import(
        self, dids_cards_were_imported_to: Iterable[DeckId], created_did: DeckId
    ) -> DeckId:
        """Remove newly created deck if the subset of local notes were in a single deck before subscribing.

        I.e., if the user has a subset of the remote deck and the entire
        subset exists in a single local deck, that deck will be used as the home deck.

        If the newly created deck is removed, move all the cards to their original deck.
        """
        dids = set(dids_cards_were_imported_to)

        # remove "Custom Study" decks from dids
        dids = {did for did in dids if not aqt.mw.col.decks.is_filtered(did)}

        # If the subset of local notes were in a single deck before the import,
        # move the new cards there (from the newly created deck) and remove the created deck.
        # Subdecks are taken into account below.
        if (dids_wh_created := dids - set([created_did])) and (
            (common_ancestor_did := lowest_level_common_ancestor_did(dids_wh_created))
        ) is not None:
            cids = aqt.mw.col.find_cards(f'deck:"{aqt.mw.col.decks.name(created_did)}"')
            aqt.mw.col.set_deck(cids, common_ancestor_did)
            LOGGER.info(
                "Moved new cards to common ancestor deck.",
                common_ancestor_did=common_ancestor_did,
            )

            if created_did != common_ancestor_did:
                aqt.mw.col.decks.remove([created_did])
                LOGGER.info("Removed created deck.", created_did=created_did)

            return common_ancestor_did

        return created_did

    def _cards_to_suspend_for_note(
        self,
        note: Note,
        cards_before_changes: Collection[Card],
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
    ) -> Collection[Card]:
        if is_tag_in_list(TAG_FOR_INSTRUCTION_NOTES, note.tags):
            return []

        def new_cards() -> List[Card]:
            cids_before_changes = {c.id for c in cards_before_changes}
            result = [c for c in note.cards() if c.id not in cids_before_changes]
            return result

        if cards_before_changes:
            # If there were cards before the changes, the note already existed in Anki.
            if (
                suspend_new_cards_of_existing_notes
                == SuspendNewCardsOfExistingNotes.NEVER
            ):
                return []
            elif (
                suspend_new_cards_of_existing_notes
                == SuspendNewCardsOfExistingNotes.ALWAYS
            ):
                return new_cards()
            elif (
                suspend_new_cards_of_existing_notes
                == SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED
            ):
                if all(
                    card.queue == QUEUE_TYPE_SUSPENDED for card in cards_before_changes
                ):
                    return new_cards()
                else:
                    return []
            else:
                raise ValueError(
                    f"Unknown value for {str(SuspendNewCardsOfExistingNotes)}"
                )  # pragma: no cover
        else:
            # If there were no cards before the changes, the note didn't exist in Anki before.
            if suspend_new_cards_of_new_notes:
                return new_cards()
            else:
                return []

    def _prepare_note(
        self,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
    ) -> Tuple[Note, NoteOperation]:
        """Gets or creates a note and prepares it for import into Anki DB. Returns the note and the operation that
        should be performed on it."""
        try:
            note = aqt.mw.col.get_note(id=NoteId(note_data.anki_nid))
            note_exists = True
        except NotFoundError:
            note_type = aqt.mw.col.models.get(NotetypeId(note_data.mid))
            note = aqt.mw.col.new_note(note_type)
            note_exists = False

        if note_data.last_update_type == SuggestionType.DELETE:
            operation = NoteOperation.DELETE
        else:
            changed = self._prepare_note_inner(
                note,
                note_data,
                protected_fields,
                protected_tags,
            )
            if not note_exists:
                operation = NoteOperation.CREATE
            elif note_exists and changed:
                operation = NoteOperation.UPDATE
            elif note_exists and not changed:
                operation = NoteOperation.NO_CHANGES
            else:
                raise AssertionError("This should never happen.")  # pragma: no cover

        return note, operation

    def _prepare_note_inner(
        self,
        note: Note,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
    ) -> bool:
        """
        Updates the note with the given fields and tags.

        Takes protected fields and tags into account.
        Sets the ankihub_id field to the given ankihub_id.
        Sets the guid to the given guid.

        Returns True if anything about the Note was changed and False otherwise.

        If the Note was changed we will flush the Note.  Keeping track of this allows us
        to avoid flushing unnecessarily, which is better for performance.
        We also keep track of which notes were updated and return them in the AnkiHubImportResult.
        """
        changed_guid = self._prepare_guid(note, note_data.guid)

        changed_ankihub_id_field = self._prepare_ankihub_id_field(
            note, ankihub_nid=str(note_data.ah_nid)
        )
        changed_fields = self._prepare_fields(
            note, fields=note_data.fields, protected_fields=protected_fields
        )
        changed_tags = self._prepare_tags(
            note,
            tags=note_data.tags,
            protected_tags=protected_tags,
        )
        changed = (
            changed_guid or changed_ankihub_id_field or changed_fields or changed_tags
        )

        return changed

    def _prepare_guid(self, note: Note, guid: str) -> bool:
        if note.guid == guid:
            return False

        note.guid = guid
        return True

    def _prepare_ankihub_id_field(self, note: Note, ankihub_nid: str) -> bool:
        if note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME] != ankihub_nid:
            note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME] = ankihub_nid
            return True
        return False

    def _prepare_fields(
        self,
        note: Note,
        fields: List[Field],
        protected_fields: Dict[int, List[str]],
    ) -> bool:
        if is_tag_in_list(TAG_FOR_PROTECTING_ALL_FIELDS, note.tags):
            return False

        changed = False
        fields_protected_by_tags = get_fields_protected_by_tags(note)
        for field_name in note.keys():
            if field_name == settings.ANKIHUB_NOTE_TYPE_FIELD_NAME:
                continue
            field = next(
                (f for f in fields if f.name == field_name),
                Field(name=field_name, value=""),
            )
            protected_fields_for_model = protected_fields.get(
                aqt.mw.col.models.get(note.mid)["id"], []
            )
            if field.name in protected_fields_for_model:
                continue

            if field.name in fields_protected_by_tags:
                continue

            if note[field.name] != field.value:
                note[field.name] = field.value
                changed = True
        return changed

    def _prepare_tags(
        self,
        note: Note,
        tags: List[str],
        protected_tags: List[str],
    ) -> bool:
        changed = False
        prev_tags = note.tags
        note.tags = _updated_tags(
            cur_tags=note.tags, incoming_tags=tags, protected_tags=protected_tags
        )
        if set(prev_tags) != set(note.tags):
            changed = True

        return changed


def _adjust_deck(deck_name: str, local_did: Optional[DeckId] = None) -> DeckId:
    unique_name = get_unique_ankihub_deck_name(deck_name)
    if local_did is None:
        local_did = DeckId(aqt.mw.col.decks.add_normal_deck_with_name(unique_name).id)
        LOGGER.info("Created deck.", local_did=local_did)
    elif aqt.mw.col.decks.name_if_exists(local_did) is None:
        # The deck created here may be removed later.
        create_deck_with_id(unique_name, local_did)
        LOGGER.info("Recreated deck.", local_did=local_did)

    return local_did


def _updated_tags(
    cur_tags: List[str], incoming_tags: List[str], protected_tags: List[str]
) -> List[str]:
    # get subset of cur_tags that are protected
    # by being equal to a protected tag or by containing a protected tag
    # protected_tags can't contain "::" (this is enforced when the user chooses them in the webapp)
    protected = set(
        tag
        for tag in cur_tags
        if any(
            protected_tag.lower() in tag.lower().split("::")
            for protected_tag in protected_tags
        )
    )

    # keep addon internal tags
    internal = [tag for tag in cur_tags if is_internal_tag(tag)]

    # keep optional tags
    optional = [tag for tag in cur_tags if is_optional_tag(tag)]

    result = list(set(protected) | set(internal) | set(optional) | set(incoming_tags))
    return result


def _create_missing_note_types(
    remote_note_types: Dict[NotetypeId, NotetypeDict],
) -> None:
    missings_mids = set(
        mid for mid in remote_note_types.keys() if aqt.mw.col.models.get(mid) is None
    )
    for mid in missings_mids:
        new_note_type = remote_note_types[mid]
        create_note_type_with_id(new_note_type, mid)
        LOGGER.info("Created missing note type.", mid=mid)


def _rename_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = aqt.mw.col.models.get(mid)
        if local_note_type["name"] != remote_note_type["name"]:
            local_note_type["name"] = remote_note_type["name"]
            aqt.mw.col.models.ensure_name_unique(local_note_type)
            aqt.mw.col.models.update_dict(local_note_type)
            LOGGER.info("Renamed note type.", mid=mid, name=remote_note_type["name"])


def _adjust_fields(
    cur_model_fields: List[Dict], new_model_fields: List[Dict]
) -> List[Dict]:
    """
    Prepares note type fields for updates by merging fields from the current and new models.

    This function handles several operations when updating note types:
    1. Maintains field content mapping by assigning appropriate 'ord' values to matching fields
    2. Assigns high 'ord' values to new fields so they start empty
    3. Appends fields that only exist locally to the new model
    4. Ensures the ankihub_id field remains at the end

    Returns:
        Updated note type fields
    """
    new_model_fields = copy.deepcopy(new_model_fields)

    cur_model_field_map = {
        field["name"].lower(): field["ord"] for field in cur_model_fields
    }

    # Set appropriate ord values for each new field
    for new_model_field in new_model_fields:
        field_name_lower = new_model_field["name"].lower()
        if field_name_lower in cur_model_field_map:
            # If field exists in current model, preserve its ord value
            new_model_field["ord"] = cur_model_field_map[field_name_lower]
        else:
            # For new fields, set ord to a value outside the range of current fields
            new_model_field["ord"] = len(cur_model_fields) + 1

    # Append fields that only exist locally to the new model, while keeping the ankihub_id field at the end
    new_model_field_names = {field["name"].lower() for field in new_model_fields}
    only_local_fields = [
        field
        for field in cur_model_fields
        if field["name"].lower() not in new_model_field_names
    ]
    ankihub_id_field = new_model_fields[-1]
    final_fields = new_model_fields[:-1] + only_local_fields + [ankihub_id_field]

    return final_fields


def cards_by_anki_nid_dict(notes: List[Note]) -> Dict[NoteId, List[Card]]:
    result = {}
    for note in notes:
        result[NoteId(note.id)] = note.cards()
    return result
