"""Import NoteInfo objects into Anki, create/update decks and note types if necessary"""
import uuid
from dataclasses import dataclass
from pprint import pformat
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import aqt
from anki.cards import Card
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId

from . import LOGGER, settings
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import Field, NoteInfo
from .db import ankihub_db
from .note_conversion import (
    TAG_FOR_PROTECTING_ALL_FIELDS,
    get_fields_protected_by_tags,
    is_internal_tag,
    is_optional_tag,
)
from .settings import config
from .subdecks import build_subdecks_and_move_cards_to_them
from .utils import (
    create_deck_with_id,
    create_note_type_with_id,
    create_note_with_id,
    get_unique_deck_name,
    lowest_level_common_ancestor_did,
    modify_note_type_templates,
    reset_note_types_of_notes,
    truncated_list,
)


@dataclass(frozen=True)
class AnkiHubImportResult:
    ankihub_did: uuid.UUID
    anki_did: DeckId
    updated_nids: List[NoteId]
    created_nids: List[NoteId]
    skipped_nids: List[NoteId]
    first_import_of_deck: bool

    def __repr__(self):
        return pformat(self.__dict__)


class AnkiHubImporter:
    def __init__(self):
        self._created_nids: List[NoteId] = []
        self._updated_nids: List[NoteId] = []
        self._skipped_nids: List[NoteId] = []

    def import_ankihub_deck(
        self,
        ankihub_did: uuid.UUID,
        notes_data: List[NoteInfo],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        local_did: Optional[  # did that new notes should be put into if importing not for the first time
            DeckId
        ] = None,
        protected_fields: Optional[
            Dict[int, List[str]]
        ] = None,  # will be fetched from api if not provided
        protected_tags: Optional[
            List[str]
        ] = None,  # will be fetched from api if not provided
        subdecks: bool = False,
        subdecks_for_new_notes_only: bool = False,
    ) -> AnkiHubImportResult:
        """
        Used for importing an AnkiHub deck for the first time or syncing.

        When no local_did is provided this function assumes that the deck gets installed for the first time.
        Returns id of the deck future cards should be imported into - the local_did - if the import was sucessful,
        else it returns None.
        subdeck indicates whether cards should be moved into subdecks based on subdeck tags
        subdecks_for_new_notes_only indicates whether only new notes should be moved into subdecks
        """

        LOGGER.info(f"Importing ankihub deck {deck_name=} {local_did=}")

        # this is not ideal, it would be probably better to fetch all note types associated with the deck each time
        if not notes_data:
            mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
            remote_note_types = fetch_remote_note_types(mids)
        else:
            remote_note_types = fetch_remote_note_types_based_on_notes_data(notes_data)

        if protected_fields is None:
            protected_fields = AnkiHubClient().get_protected_fields(ankihub_did)

        if protected_tags is None:
            protected_tags = AnkiHubClient().get_protected_tags(ankihub_did)

        return self._import_ankihub_deck_inner(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
            deck_name=deck_name,
            remote_note_types=remote_note_types,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            local_did=local_did,
            subdecks=subdecks,
            subdecks_for_new_notes_only=subdecks_for_new_notes_only,
        )

    def _import_ankihub_deck_inner(
        self,
        ankihub_did: uuid.UUID,
        notes_data: List[NoteInfo],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        remote_note_types: Dict[NotetypeId, NotetypeDict] = {},
        protected_fields: Dict[int, List[str]] = {},
        protected_tags: List[str] = [],
        local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
        subdecks: bool = False,
        subdecks_for_new_notes_only: bool = False,
    ) -> AnkiHubImportResult:
        """ """
        # Instance attributes are reset here so that the results returned are only for the current deck.
        self._created_nids = []
        self._updated_nids = []
        self._skipped_nids = []

        self._first_import_of_deck = local_did is None
        self._protected_fields = protected_fields
        self._protected_tags = protected_tags
        self._local_did = adjust_deck(deck_name, local_did)

        adjust_note_types(remote_note_types)

        dids = self._import_notes(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
        )

        if self._first_import_of_deck:
            self._local_did = self._cleanup_first_time_deck_import(
                dids, self._local_did
            )

        if subdecks or subdecks_for_new_notes_only:
            if subdecks_for_new_notes_only:
                anki_nids = list(self._created_nids)
            else:
                anki_nids = list(self._created_nids + self._updated_nids)

            build_subdecks_and_move_cards_to_them(
                ankihub_did=ankihub_did, nids=anki_nids
            )

        result = AnkiHubImportResult(
            ankihub_did=ankihub_did,
            anki_did=self._local_did,
            created_nids=self._created_nids,
            updated_nids=self._updated_nids,
            skipped_nids=self._skipped_nids,
            first_import_of_deck=self._first_import_of_deck,
        )
        return result

    def _import_notes(
        self,
        ankihub_did: uuid.UUID,
        notes_data: List[NoteInfo],
    ) -> Set[DeckId]:
        # returns set of ids of decks notes were imported into

        upserted_notes, skipped_notes = ankihub_db.upsert_notes_data(
            ankihub_did=ankihub_did, notes_data=notes_data
        )
        self._skipped_nids = [NoteId(note_data.anki_nid) for note_data in skipped_notes]

        reset_note_types_of_notes_based_on_notes_data(upserted_notes)

        dids: Set[DeckId] = set()  # set of ids of decks notes were imported into
        for note_data in upserted_notes:
            note = self._update_or_create_note(
                note_data=note_data,
                anki_did=self._local_did,
                protected_fields=self._protected_fields,
                protected_tags=self._protected_tags,
            )
            dids_for_note = set(c.did for c in note.cards())
            dids = dids | dids_for_note

        ankihub_db.transfer_mod_values_from_anki_db(notes_data=upserted_notes)

        LOGGER.info(
            f"Created {len(self._created_nids)} notes: {truncated_list(self._created_nids, limit=50)}"
        )
        LOGGER.info(
            f"Updated {len(self._updated_nids)} notes: {truncated_list(self._updated_nids, limit=50)}"
        )
        LOGGER.info(
            f"Skippped {len(self._skipped_nids)} notes: "
            f"{truncated_list(self._skipped_nids, limit=50)}"
        )

        return dids

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

            aqt.mw.col.decks.remove([created_did])
            LOGGER.info(f"Removed created deck {created_did=}")
            return common_ancestor_did

        return created_did

    def _update_or_create_note(
        self,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        anki_did: Optional[DeckId] = None,
    ) -> Note:
        LOGGER.debug(
            f"Trying to update or create note: {note_data.anki_nid=}, {note_data.ankihub_note_uuid=}"
        )

        try:
            # Get the note before changes are made below so that we can check if new
            # were created and suspend them below if necessary.
            note_before_changes = aqt.mw.col.get_note(NoteId(note_data.anki_nid))
        except NotFoundError:
            note_before_changes = None
        cards_before_changes = (
            note_before_changes.cards() if note_before_changes else []
        )

        note = self._update_or_create_note_inner(
            note_data,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            anki_did=anki_did,
        )

        self._maybe_suspend_new_cards(note, cards_before_changes)

        return note

    def _maybe_suspend_new_cards(
        self, note: Note, cards_before_changes: List[Card]
    ) -> None:
        if not cards_before_changes:
            return

        def new_cards():
            return [
                c
                for c in note.cards()
                if c.id not in [c.id for c in cards_before_changes]
            ]

        def suspend_new_cards():
            if not (new_cards_ := new_cards()):
                return

            LOGGER.info(f"Suspending new cards of note {note.id}")
            for card in new_cards_:
                card.queue = QUEUE_TYPE_SUSPENDED
                card.flush()

        config_value = config.public_config["suspend_new_cards_of_existing_notes"]
        if config_value == "never":
            return
        elif config_value == "always":
            suspend_new_cards()
        elif config_value == "if_siblings_are_suspended":
            if all(card.queue == QUEUE_TYPE_SUSPENDED for card in cards_before_changes):
                suspend_new_cards()
        else:
            raise ValueError("Invalid suspend_new_cards config value")

    def _update_or_create_note_inner(
        self,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        anki_did: Optional[DeckId],  # only relevant for newly created notes
    ) -> Note:
        # Create a copy to avoid mutating note_data.fields.
        # TODO it seems that fields is not used and we can remove it.
        fields = note_data.fields.copy()

        try:
            note = aqt.mw.col.get_note(id=NoteId(note_data.anki_nid))
            fields.append(
                Field(
                    name=settings.ANKIHUB_NOTE_TYPE_FIELD_NAME,
                    order=len(fields),
                    value=str(note_data.ankihub_note_uuid),
                )
            )
            # TODO Refactor so that self.prepare_note is not called in two places.
            note_prepared = self.prepare_note(
                note,
                note_data,
                protected_fields,
                protected_tags,
            )
            if note_prepared:
                note.flush()
                self._updated_nids.append(note.id)
                LOGGER.debug(f"Updated note: {note_data.anki_nid=}")
            else:
                LOGGER.debug(f"No changes, skipping {note_data.anki_nid=}")
        except NotFoundError:
            if anki_did is None:
                raise ValueError("anki_did must be set for new notes")

            note_type = aqt.mw.col.models.get(NotetypeId(note_data.mid))
            note = aqt.mw.col.new_note(note_type)
            self.prepare_note(
                note,
                note_data,
                protected_fields,
                protected_tags,
            )
            note = create_note_with_id(
                note, anki_id=NoteId(note_data.anki_nid), anki_did=anki_did
            )
            self._created_nids.append(note.id)
            LOGGER.debug(f"Created note: {note_data.anki_nid=}")
        return note

    def prepare_note(
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
            note, ankihub_nid=str(note_data.ankihub_note_uuid)
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
        note.tags = updated_tags(
            cur_tags=note.tags, incoming_tags=tags, protected_tags=protected_tags
        )
        if set(prev_tags) != set(note.tags):
            LOGGER.debug(
                f"Tags were changed from {prev_tags} to {note.tags}.",
            )
            changed = True

        return changed


def adjust_deck(deck_name: str, local_did: Optional[DeckId] = None) -> DeckId:
    unique_name = get_unique_deck_name(deck_name)
    if local_did is None:
        local_did = DeckId(aqt.mw.col.decks.add_normal_deck_with_name(unique_name).id)
        LOGGER.info(f"Created deck {local_did=}")
    elif aqt.mw.col.decks.name_if_exists(local_did) is None:
        # The deck created here may be removed later.
        create_deck_with_id(unique_name, local_did)
        LOGGER.info(f"Recreated deck {local_did=}")

    return local_did


def updated_tags(
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


def fetch_remote_note_types_based_on_notes_data(
    notes_data: List[NoteInfo],
) -> Dict[NotetypeId, NotetypeDict]:
    remote_mids = set(NotetypeId(note_data.mid) for note_data in notes_data)
    result = fetch_remote_note_types(remote_mids)
    return result


def fetch_remote_note_types(
    mids: Iterable[NotetypeId],
) -> Dict[NotetypeId, NotetypeDict]:
    client = AnkiHubClient()
    result = {mid: client.get_note_type(mid) for mid in mids}
    return result


def adjust_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    # can be called when installing a deck for the first time and when synchronizing with AnkiHub

    LOGGER.info("Beginning adjusting note types...")

    create_missing_note_types(remote_note_types)
    rename_note_types(remote_note_types)
    ensure_local_and_remote_fields_are_same(remote_note_types)
    modify_note_type_templates(remote_note_types.keys())

    LOGGER.info("Adjusted note types.")


def create_missing_note_types(
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


def rename_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = aqt.mw.col.models.get(mid)
        if local_note_type["name"] != remote_note_type["name"]:
            local_note_type["name"] = remote_note_type["name"]
            aqt.mw.col.models.ensure_name_unique(local_note_type)
            aqt.mw.col.models.update_dict(local_note_type)
            LOGGER.info(f"Renamed note type {mid=} to {local_note_type['name']}")


def ensure_local_and_remote_fields_are_same(
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

        local_note_type["flds"] = adjust_field_ords(
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


def adjust_field_ords(
    cur_model_flds: List[Dict], new_model_flds: List[Dict]
) -> List[Dict]:
    # This makes sure that when fields get added or are moved field contents end up
    # in the field with the same name as before.
    # This is relevant because people can protect fields.
    # Note that the result will have exactly the same set of field names as the new_model,
    # just the ords will be adjusted.
    for fld in new_model_flds:
        if (
            cur_ord := next(
                (_fld["ord"] for _fld in cur_model_flds if _fld["name"] == fld["name"]),
                None,
            )
        ) is not None:
            fld["ord"] = cur_ord
        else:
            # it's okay to assign this to multiple fields because the
            # backend assigns new ords equal to the fields index
            fld["ord"] = len(new_model_flds) - 1
    return new_model_flds


def reset_note_types_of_notes_based_on_notes_data(
    notes_data: Sequence[NoteInfo],
) -> None:
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
    nid_mid_pairs = [
        (NoteId(note_data.anki_nid), NotetypeId(note_data.mid))
        for note_data in notes_data
    ]
    reset_note_types_of_notes(nid_mid_pairs)
