"""Import NoteInfo objects and note types into Anki and the AnkiHub database,
create/update decks and note types in the Anki collection if necessary"""

import textwrap
import uuid
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import Collection, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
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
from ..settings import DeleteNoteOnRemoteDelete, SuspendNewCardsOfExistingNotes
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
    lowest_level_common_ancestor_did,
    modify_note_type_templates,
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
        delete_note_on_remote_delete: DeleteNoteOnRemoteDelete,
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
        anki_did: Optional[  # did that new notes should be put into if importing not for the first time
            DeckId
        ] = None,
        subdecks: bool = False,
        subdecks_for_new_notes_only: bool = False,
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
            textwrap.dedent(
                f"""
                Importing ankihub deck {deck_name=} {is_first_import_of_deck=} {ankihub_did=} {anki_did=}
                \tNotes: {pformat(truncated_list(notes, 2))}
                \tProtected fields: {pformat(protected_fields)}
                \tProtected tags: {pformat(protected_tags)}
                \tSubdecks: {subdecks}, Subdecks for new notes only: {subdecks_for_new_notes_only}
                """
            ).strip("\n")
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

        if self._is_first_import_of_deck:
            # Clean up any left over data for this deck in the ankihub database from previous deck imports.
            ankihub_db.remove_deck(ankihub_did)

        self._import_note_types(note_types=note_types)

        dids = self._import_notes(
            notes_data=notes,
            delete_note_on_remote_delete=delete_note_on_remote_delete,
            suspend_new_cards_of_new_notes=suspend_new_cards_of_new_notes,
            suspend_new_cards_of_existing_notes=suspend_new_cards_of_existing_notes,
        )

        if self._is_first_import_of_deck:
            self._local_did = self._cleanup_first_time_deck_import(
                dids, self._local_did
            )

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
        _adjust_note_types_in_anki_db(note_types)

    def _import_note_types_into_ankihub_db(
        self, note_types: Dict[NotetypeId, NotetypeDict]
    ) -> None:
        for note_type in note_types.values():
            ankihub_db.upsert_note_type(
                ankihub_did=self._ankihub_did, note_type=note_type
            )

    def _import_notes(
        self,
        notes_data: List[NoteInfo],
        delete_note_on_remote_delete: DeleteNoteOnRemoteDelete,
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

        # Upsert notes into Anki DB, delete them or mark them as deleted
        _reset_note_types_of_notes_based_on_notes_data(upserted_notes_data)

        (
            notes_to_create_by_ah_nid,
            notes_to_update,
            notes_to_delete,
            notes_without_changes,
        ) = self._prepare_notes(notes_data=upserted_notes_data)

        cards_by_anki_nid_before_import = cards_by_anki_nid_dict(notes_to_update)

        self._update_notes(notes_to_update)
        self._create_notes(notes_to_create_by_ah_nid, notes_data=upserted_notes_data)
        self._delete_notes_or_mark_as_deleted(
            notes_to_delete, delete_note_on_remote_delete=delete_note_on_remote_delete
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

    def _log_note_import_summary(self) -> None:
        LOGGER.info(
            f"Created {len(self._created_nids)} notes: {truncated_list(self._created_nids, limit=50)}"
        )
        LOGGER.info(
            f"Updated {len(self._updated_nids)} notes: {truncated_list(self._updated_nids, limit=50)}"
        )
        LOGGER.info(
            f"Deleted {len(self._deleted_nids)} notes: {truncated_list(self._deleted_nids, limit=50)}"
        )
        LOGGER.info(
            textwrap.dedent(
                f"""
                Marked {len(self._marked_as_deleted_nids)} notes as deleted:
                {truncated_list(self._marked_as_deleted_nids, limit=50)}
                """
            ).strip()
        )
        LOGGER.info(
            f"Skippped {len(self._skipped_nids)} notes: "
            f"{truncated_list(self._skipped_nids, limit=50)}"
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
        notes_to_delete: Collection[Note],
        delete_note_on_remote_delete: DeleteNoteOnRemoteDelete,
    ) -> None:
        if not notes_to_delete:
            return

        if delete_note_on_remote_delete == DeleteNoteOnRemoteDelete.IF_NOT_REVIEWED_YET:
            nids = [note.id for note in notes_to_delete]
            nids_of_notes_with_reviews: Set[NoteId] = set(
                aqt.mw.col.db.list(
                    "SELECT DISTINCT nid FROM cards "
                    f"WHERE nid IN {ids2str(nids)} AND "
                    "id IN (SELECT DISTINCT cid FROM revlog)"
                )
            )
            notes_with_reviews = set(
                note
                for note in notes_to_delete
                if note.id in nids_of_notes_with_reviews
            )
            notes_without_reviews = set(notes_to_delete) - notes_with_reviews

            self._mark_notes_as_deleted(notes_with_reviews)
            self._delete_notes(notes_without_reviews)

        elif delete_note_on_remote_delete == DeleteNoteOnRemoteDelete.NEVER:
            self._mark_notes_as_deleted(notes_to_delete)
        else:
            raise ValueError(  # pragma: no cover
                f"Unknown value for {str(DeleteNoteOnRemoteDelete)}"
            )

    def _delete_notes(self, notes: Collection[Note]) -> None:
        """Delete notes from the Anki database. Updates the _deleted_nids attribute."""
        if not notes:
            return

        changes = aqt.mw.col.remove_notes([note.id for note in notes])
        nids_to_delete = [note.id for note in notes]
        LOGGER.info(
            f"Deleted {changes.count} notes: {truncated_list(nids_to_delete, limit=50)}"
        )
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

        LOGGER.info(
            f"Marked {len(notes)} notes as deleted: {truncated_list(nids, limit=50)}"
        )

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
            f"Suspended {len(cards_to_suspend)} cards: {truncated_list(cards_to_suspend, limit=50)}"
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
                f"Moved new cards to common ancestor deck {common_ancestor_did=}"
            )

            if created_did != common_ancestor_did:
                aqt.mw.col.decks.remove([created_did])
                LOGGER.info(f"Removed created deck {created_did=}")

            return common_ancestor_did

        return created_did

    def _cards_to_suspend_for_note(
        self,
        note: Note,
        cards_before_changes: Collection[Card],
        suspend_new_cards_of_new_notes: bool,
        suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes,
    ) -> Collection[Card]:
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

        LOGGER.debug(f"Prepared note: {note_data.anki_nid=} {operation=}")
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

        LOGGER.debug("Preparing note...")

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

        LOGGER.debug(f"Prepared note. {changed=}")
        return changed

    def _prepare_guid(self, note: Note, guid: str) -> bool:
        if note.guid == guid:
            return False

        LOGGER.debug(f"Changing guid of note {note.id} from {note.guid} to {guid}")
        note.guid = guid
        return True

    def _prepare_ankihub_id_field(self, note: Note, ankihub_nid: str) -> bool:
        if note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME] != ankihub_nid:
            LOGGER.debug(
                f"AnkiHub id of note {note.id} will be changed from {note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME]} "
                f"to {ankihub_nid}",
            )
            note[settings.ANKIHUB_NOTE_TYPE_FIELD_NAME] = ankihub_nid
            return True
        return False

    def _prepare_fields(
        self,
        note: Note,
        fields: List[Field],
        protected_fields: Dict[int, List[str]],
    ) -> bool:
        if TAG_FOR_PROTECTING_ALL_FIELDS in note.tags:
            LOGGER.debug(
                "Skipping preparing fields because they are protected by a tag."
            )
            return False

        changed = False
        fields_protected_by_tags = get_fields_protected_by_tags(note)
        for field in fields:
            protected_fields_for_model = protected_fields.get(
                aqt.mw.col.models.get(note.mid)["id"], []
            )
            if field.name in protected_fields_for_model:
                LOGGER.debug(
                    f"Field {field.name} is protected by the protected_fields for the model, skipping."
                )
                continue

            if field.name in fields_protected_by_tags:
                LOGGER.debug(f"Field {field.name} is protected by a tag, skipping.")
                continue

            if note[field.name] != field.value:
                LOGGER.debug(
                    f'Field: "{field.name}" will be changed from:\n'
                    f"{note[field.name]}\n"
                    "to:\n"
                    f"{field.value}"
                )
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
            LOGGER.debug(
                f"Tags were changed from {prev_tags} to {note.tags}.",
            )
            changed = True

        return changed


def _adjust_deck(deck_name: str, local_did: Optional[DeckId] = None) -> DeckId:
    unique_name = get_unique_ankihub_deck_name(deck_name)
    if local_did is None:
        local_did = DeckId(aqt.mw.col.decks.add_normal_deck_with_name(unique_name).id)
        LOGGER.info(f"Created deck {local_did=}")
    elif aqt.mw.col.decks.name_if_exists(local_did) is None:
        # The deck created here may be removed later.
        create_deck_with_id(unique_name, local_did)
        LOGGER.info(f"Recreated deck {local_did=}")

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
        if any(protected_tag in tag.split("::") for protected_tag in protected_tags)
    )

    # keep addon internal tags
    internal = [tag for tag in cur_tags if is_internal_tag(tag)]

    # keep optional tags
    optional = [tag for tag in cur_tags if is_optional_tag(tag)]

    result = list(set(protected) | set(internal) | set(optional) | set(incoming_tags))
    return result


def _adjust_note_types_in_anki_db(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    # can be called when installing a deck for the first time and when synchronizing with AnkiHub

    LOGGER.info("Beginning adjusting note types...")

    _create_missing_note_types(remote_note_types)
    _rename_note_types(remote_note_types)
    _ensure_local_and_remote_fields_are_same(remote_note_types)
    modify_note_type_templates(remote_note_types.keys())

    LOGGER.info("Adjusted note types.")


def _create_missing_note_types(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    missings_mids = set(
        mid for mid in remote_note_types.keys() if aqt.mw.col.models.get(mid) is None
    )
    for mid in missings_mids:
        LOGGER.info(f"Missing note type {mid}")
        new_note_type = remote_note_types[mid]
        create_note_type_with_id(new_note_type, mid)
        LOGGER.info(f"Created missing note type {mid}")


def _rename_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = aqt.mw.col.models.get(mid)
        if local_note_type["name"] != remote_note_type["name"]:
            local_note_type["name"] = remote_note_type["name"]
            aqt.mw.col.models.ensure_name_unique(local_note_type)
            aqt.mw.col.models.update_dict(local_note_type)
            LOGGER.info(f"Renamed note type {mid=} to {local_note_type['name']}")


def _ensure_local_and_remote_fields_are_same(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    def field_tuples(flds: List[Dict]) -> List[Tuple[int, str]]:
        return [(field["ord"], field["name"]) for field in flds]

    note_types_with_field_conflicts: List[Tuple[NotetypeDict, NotetypeDict]] = []
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = aqt.mw.col.models.get(mid)

        if not field_tuples(local_note_type["flds"]) == field_tuples(
            remote_note_type["flds"]
        ):
            LOGGER.info(
                f'Fields of local note type "{local_note_type["name"]}" differ from remote note type.\n'
                f"local:\n{pformat(field_tuples(local_note_type['flds']))}\n"
                f"remote:\n{pformat(field_tuples(remote_note_type['flds']))}"
            )
            note_types_with_field_conflicts.append((local_note_type, remote_note_type))

    for local_note_type, remote_note_type in note_types_with_field_conflicts:
        LOGGER.info(f"Adjusting fields of {local_note_type['name']}...")

        local_note_type["flds"] = _adjust_field_ords(
            local_note_type["flds"], remote_note_type["flds"]
        )
        LOGGER.info(
            f"Fields after adjusting ords:\n{pformat(field_tuples(local_note_type['flds']))}"
        )

        aqt.mw.col.models.update_dict(local_note_type)
        LOGGER.info(
            f"Fields after updating the model:\n"
            f"{pformat(field_tuples(aqt.mw.col.models.get(local_note_type['id'])['flds']))}"
        )


def _adjust_field_ords(
    cur_model_flds: List[Dict], new_model_flds: List[Dict]
) -> List[Dict]:
    """This makes sure that when fields get added or are moved field contents end up
    in the field with the same name as before.
    Note that the result will have exactly the same field names in the same order as the new_model,
    just the ords of the fields will be adjusted.
    """
    # By setting the ord value of a field to x we cause Anki to move the contents of current field x
    # to this field.
    for new_field in new_model_flds:
        if (
            cur_ord := next(
                (
                    old_field["ord"]
                    for old_field in cur_model_flds
                    if old_field["name"].lower() == new_field["name"].lower()
                ),
                None,
            )
        ) is not None:
            # If a field with the same name exists in the current model, use its ord.
            new_field["ord"] = cur_ord
        else:
            # If a field with the same name doesn't exist in the current model, we don't wan't Anki to
            # move the contents of any current field to this field, so we set the ord to a value that
            # is larger than the number of fields in the current model. This way the contents of this
            # field will be empty.
            new_field["ord"] = len(cur_model_flds) + 1
    return new_model_flds


def _reset_note_types_of_notes_based_on_notes_data(
    notes_data: Sequence[NoteInfo],
) -> None:
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
    nid_mid_pairs = [
        (NoteId(note_data.anki_nid), NotetypeId(note_data.mid))
        for note_data in notes_data
    ]
    change_note_types_of_notes(nid_mid_pairs)


def cards_by_anki_nid_dict(notes: List[Note]) -> Dict[NoteId, List[Card]]:
    result = {}
    for note in notes:
        result[NoteId(note.id)] = note.cards()
    return result
