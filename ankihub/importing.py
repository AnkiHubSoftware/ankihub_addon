"""Import NoteInfo objects into Anki, create/update decks and note types if necessary"""

import uuid
from pprint import pformat
from typing import Dict, Iterable, List, Optional, Set, Tuple

from anki.cards import Card
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt import mw

from . import LOGGER, settings
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import Field, NoteInfo, SuggestionType
from .db import ankihub_db
from .note_conversion import (
    TAG_FOR_NEW_NOTE,
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_SUGGESTION_TYPE,
    get_fields_protected_by_tags,
    is_internal_tag,
)
from .settings import config
from .utils import (
    create_deck_with_id,
    create_note_type_with_id,
    create_note_with_id,
    get_unique_deck_name,
    lowest_level_common_ancestor_did,
    modify_note_type_templates,
    reset_note_types_of_notes,
)


class AnkiHubImporter:
    def __init__(self):
        self.num_notes_updated = 0
        self.num_notes_created = 0

    def import_ankihub_deck(
        self,
        ankihub_did: str,
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
    ) -> DeckId:
        """
        Used for importing an ankihub deck and updates to an ankihub deck
        When no local_did is provided this function assumes that the deck gets installed for the first time
        Returns id of the deck future cards should be imported into - the local_did - if the import was sucessful
        else it returns None
        """

        LOGGER.debug(f"Importing ankihub deck {deck_name=} {local_did=}")

        remote_note_types = fetch_remote_note_types_based_on_notes_data(notes_data)

        if protected_fields is None:
            protected_fields = AnkiHubClient().get_protected_fields(
                uuid.UUID(ankihub_did)
            )

        if protected_tags is None:
            protected_tags = AnkiHubClient().get_protected_tags(uuid.UUID(ankihub_did))

        anki_deck_id = self._import_ankihub_deck_inner(
            ankihub_did=ankihub_did,
            notes_data=notes_data,
            deck_name=deck_name,
            remote_note_types=remote_note_types,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            local_did=local_did,
        )
        return anki_deck_id

    def _import_ankihub_deck_inner(
        self,
        ankihub_did: str,
        notes_data: List[NoteInfo],
        deck_name: str,  # name that will be used for a deck if a new one gets created
        remote_note_types: Dict[NotetypeId, NotetypeDict],
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
    ) -> DeckId:
        first_import_of_deck = local_did is None

        local_did = adjust_deck(deck_name, local_did)
        adjust_note_types(remote_note_types)
        reset_note_types_of_notes_based_on_notes_data(notes_data)

        dids: Set[DeckId] = set()  # set of ids of decks notes were imported into
        for note_data in notes_data:
            note = self._update_or_create_note(
                note_data=note_data,
                anki_did=local_did,
                protected_fields=protected_fields,
                protected_tags=protected_tags,
                first_import_of_deck=first_import_of_deck,
            )

            dids_for_note = set(c.did for c in note.cards())
            dids = dids | dids_for_note

        if first_import_of_deck:
            local_did = self._cleanup_first_time_deck_import(dids, local_did)

        ankihub_db.save_notes_data_and_mod_values(
            ankihub_did=ankihub_did, notes_data=notes_data
        )

        return local_did

    def _cleanup_first_time_deck_import(
        self, dids_cards_were_imported_to: Iterable[DeckId], created_did: DeckId
    ) -> DeckId:
        dids = set(dids_cards_were_imported_to)

        # remove "Custom Study" decks from dids
        dids = {did for did in dids if not mw.col.decks.is_filtered(did)}

        # if there is a single deck where all the existing cards were before the import,
        # move the new cards there (from the newly created deck) and remove the created deck
        # takes subdecks into account
        if (dids_wh_created := dids - set([created_did])) and (
            (common_ancestor_did := lowest_level_common_ancestor_did(dids_wh_created))
        ) is not None:
            cids = mw.col.find_cards(f'deck:"{mw.col.decks.name(created_did)}"')
            mw.col.set_deck(cids, common_ancestor_did)
            LOGGER.debug(
                f"Moved new cards to common ancestor deck {common_ancestor_did=}"
            )

            mw.col.decks.remove([created_did])
            LOGGER.debug(f"Removed created deck {created_did=}")
            return common_ancestor_did

        return created_did

    def _update_or_create_note(
        self,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        anki_did: Optional[DeckId] = None,
        first_import_of_deck: bool = False,
    ) -> Note:
        LOGGER.debug(
            f"Trying to update or create note: {note_data.anki_nid=}, {note_data.ankihub_note_uuid=}"
        )

        note_before_changes = None
        try:
            note_before_changes = mw.col.get_note(NoteId(note_data.anki_nid))
        except NotFoundError:
            pass
        cards_before_changes = (
            note_before_changes.cards() if note_before_changes else []
        )

        note = self._update_or_create_note_inner(
            note_data,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
            anki_did=anki_did,
            first_import_of_deck=first_import_of_deck,
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

            LOGGER.debug(f"Suspending new cards of note {note.id}")
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
        first_import_of_deck: bool,
    ) -> Note:
        fields = note_data.fields

        try:
            note = mw.col.get_note(id=NoteId(note_data.anki_nid))
            fields.append(
                Field(
                    name=settings.ANKIHUB_NOTE_TYPE_FIELD_NAME,
                    order=len(fields),
                    value=str(note_data.ankihub_note_uuid),
                )
            )
            if self.prepare_note(
                note,
                note_data,
                protected_fields,
                protected_tags,
                first_import_of_deck,
            ):
                note.flush()
                self.num_notes_updated += 1
                LOGGER.debug(f"Updated note: {note_data.anki_nid=}")
            else:
                LOGGER.debug(f"No changes, skipping {note_data.anki_nid=}")
        except NotFoundError:
            if anki_did is None:
                raise ValueError("anki_did must be set for new notes")

            note_type = mw.col.models.get(NotetypeId(note_data.mid))
            note = mw.col.new_note(note_type)
            self.prepare_note(
                note,
                note_data,
                protected_fields,
                protected_tags,
                first_import_of_deck,
            )
            note = create_note_with_id(
                note, anki_id=NoteId(note_data.anki_nid), anki_did=anki_did
            )
            self.num_notes_created += 1
            LOGGER.debug(f"Created note: {note_data.anki_nid=}")
        return note

    def prepare_note(
        self,
        note: Note,
        note_data: NoteInfo,
        protected_fields: Dict[int, List[str]],
        protected_tags: List[str],
        first_import_of_deck: bool,
    ) -> bool:
        """
        Updates the note with the given fields and tags (taking protected fields and tags into account)
        Sets the ankihub_id field to the given ankihub_id
        Sets the guid to the given guid
        Returns True if note was changed and False otherwise
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

        self._prepare_internal_tags(
            note=note,
            first_import_of_deck=first_import_of_deck,
            last_update_type=note_data.last_update_type,
            note_changed=changed,
        )

        LOGGER.debug(f"Prepared note. {changed=}")
        return changed

    def _prepare_guid(self, note: Note, guid: str) -> bool:
        if note.guid == guid:
            return False

        LOGGER.debug(f"Changing guid of note {note.id} from {note.guid} to {guid}")
        note.guid = guid
        return True

    def _prepare_internal_tags(
        self,
        note: Note,
        first_import_of_deck: bool,
        last_update_type: Optional[SuggestionType],
        note_changed: bool,
    ):
        if (
            first_import_of_deck
            or not note_changed
            or (note.id != 0 and not last_update_type)
        ):
            return

        # add special tag if note is new
        if note.id == 0:
            update_tag = TAG_FOR_NEW_NOTE

        # add special tag if note was updated
        elif note.id != 0:
            update_tag = TAG_FOR_SUGGESTION_TYPE[last_update_type]

        if update_tag not in note.tags:
            note.tags += [update_tag]
            LOGGER.debug(f'Added "{update_tag}" to tags of note.')

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
                mw.col.models.get(note.mid)["id"], []
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
        local_did = DeckId(mw.col.decks.add_normal_deck_with_name(unique_name).id)
        LOGGER.debug(f"Created deck {local_did=}")
    elif mw.col.decks.name_if_exists(local_did) is None:
        # recreate deck if it was deleted
        create_deck_with_id(unique_name, local_did)
        LOGGER.debug(f"Recreated deck {local_did=}")

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

    result = list(set(protected) | set(internal) | set(incoming_tags))
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

    LOGGER.debug("Beginning adjusting note types...")

    create_missing_note_types(remote_note_types)
    rename_note_types(remote_note_types)
    ensure_local_and_remote_fields_are_same(remote_note_types)
    modify_note_type_templates(remote_note_types.keys())

    LOGGER.debug("Adjusted note types.")


def create_missing_note_types(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    missings_mids = set(
        mid for mid in remote_note_types.keys() if mw.col.models.get(mid) is None
    )
    for mid in missings_mids:
        LOGGER.debug(f"Missing note type {mid}")
        new_note_type = remote_note_types[mid]
        create_note_type_with_id(new_note_type, mid)
        LOGGER.debug(f"Created missing note type {mid}")


def rename_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = mw.col.models.get(mid)
        if local_note_type["name"] != remote_note_type["name"]:
            local_note_type["name"] = remote_note_type["name"]
            mw.col.models.ensure_name_unique(local_note_type)
            mw.col.models.update_dict(local_note_type)
            LOGGER.debug(f"Renamed note type {mid=} to {local_note_type['name']}")


def ensure_local_and_remote_fields_are_same(
    remote_note_types: Dict[NotetypeId, NotetypeDict]
) -> None:
    def field_tuples(flds: List[Dict]) -> List[Tuple[int, str]]:
        return [(field["ord"], field["name"]) for field in flds]

    note_types_with_field_conflicts: List[Tuple[NotetypeDict, NotetypeDict]] = []
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = mw.col.models.get(mid)

        if not field_tuples(local_note_type["flds"]) == field_tuples(
            remote_note_type["flds"]
        ):
            LOGGER.debug(
                f'Fields of local note type "{local_note_type["name"]}" differ from remote note type.\n'
                f"local:\n{pformat(field_tuples(local_note_type['flds']))}\n"
                f"remote:\n{pformat(field_tuples(remote_note_type['flds']))}"
            )
            note_types_with_field_conflicts.append((local_note_type, remote_note_type))

    for local_note_type, remote_note_type in note_types_with_field_conflicts:
        LOGGER.debug(f"Adjusting fields of {local_note_type['name']}...")

        local_note_type["flds"] = adjust_field_ords(
            local_note_type["flds"], remote_note_type["flds"]
        )
        LOGGER.debug(
            f"Fields after adjusting ords:\n{pformat(field_tuples(local_note_type['flds']))}"
        )

        mw.col.models.update_dict(local_note_type)
        LOGGER.debug(
            f"Fields after updating the model:\n"
            f"{pformat(field_tuples(mw.col.models.get(local_note_type['id'])['flds']))}"
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


def reset_note_types_of_notes_based_on_notes_data(notes_data: List[NoteInfo]) -> None:
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
    nid_mid_pairs = [
        (NoteId(note_data.anki_nid), NotetypeId(note_data.mid))
        for note_data in notes_data
    ]
    reset_note_types_of_notes(nid_mid_pairs)
