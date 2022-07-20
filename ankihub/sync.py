import json
import uuid
from concurrent.futures import Future
from pprint import pformat
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError

from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt import gui_hooks, mw
from aqt.utils import tooltip
from requests.exceptions import ConnectionError

from . import LOGGER, constants, report_exception
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config
from .utils import (
    create_backup_with_progress,
    create_deck_with_id,
    create_note_type_with_id,
    create_note_with_id,
    get_unique_deck_name,
    lowest_level_common_ancestor_did,
    modify_note_type_templates,
    reset_note_types_of_notes,
)


def sync_with_ankihub() -> None:
    LOGGER.debug("Trying to sync with AnkiHub.")

    create_backup_with_progress()

    client = AnkiHubClient()
    decks = config.private_config.decks
    for ankihub_did, deck in decks.items():
        notes_data = []
        for response in client.get_deck_updates(
            ankihub_did, since=deck["latest_update"]
        ):
            if response.status_code != 200:
                return

            data = response.json()
            notes = data["notes"]
            if notes:
                notes_data += notes

        if notes_data:
            import_ankihub_deck(
                ankihub_did=ankihub_did,
                notes_data=notes_data,
                deck_name=deck["name"],
                local_did=deck["anki_id"],
            )
            config.save_latest_update(ankihub_did, data["latest_update"])
        else:
            LOGGER.debug(f"No new updates to sync for {ankihub_did=}")


def import_ankihub_deck(
    ankihub_did: str,
    notes_data: List[dict],
    deck_name: str,  # name that will be used for a deck if a new one gets created
    local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
) -> Optional[DeckId]:

    remote_note_types = fetch_remote_note_types_based_on_notes_data(notes_data)

    client = AnkiHubClient()
    protected_fields = None
    response = client.get_protected_fields(uuid.UUID(ankihub_did))
    if response.status_code == 200:
        protected_fields = response.json()["fields"]
    elif response.status_code == 404:
        protected_fields = {}
    else:
        return None

    protected_tags = None
    response = client.get_protected_tags(uuid.UUID(ankihub_did))
    if response.status_code == 200:
        protected_tags = response.json()["tags"]
    elif response.status_code == 404:
        protected_tags = []
    else:
        return None

    anki_deck_id = import_ankihub_deck_inner(
        notes_data=notes_data,
        deck_name=deck_name,
        remote_note_types=remote_note_types,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
        local_did=local_did,
    )
    return anki_deck_id


def import_ankihub_deck_inner(
    notes_data: List[dict],
    deck_name: str,  # name that will be used for a deck if a new one gets created
    remote_note_types: Dict[NotetypeId, NotetypeDict],
    protected_fields: Dict[str, List[str]],
    protected_tags: List[str],
    local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
) -> DeckId:
    """
    Used for importing an ankihub deck and updates to an ankihub deck
    When no local_did is provided this function assumes that the deck gets installed for the first time
    Returns id of the deck future cards should be imported into - the local_did
    """

    LOGGER.debug(f"Importing ankihub deck {deck_name=} {local_did=}")

    first_time_import = local_did is None

    local_did = adjust_deck(deck_name, local_did)
    adjust_note_types(remote_note_types)
    reset_note_types_of_notes_based_on_notes_data(notes_data)

    # TODO fix differences between csv when installing for the first time vs. when updating
    # on the AnkiHub side
    # for example for one the fields name is "note_id" and for the other "id"
    dids: Set[DeckId] = set()  # set of ids of decks notes were imported into
    for note_data in notes_data:
        LOGGER.debug(f"Trying to update or create note:\n {pformat(note_data)}")
        note = update_or_create_note(
            anki_id=NoteId(int((note_data["anki_id"]))),
            ankihub_id=note_data.get("id")
            if note_data.get("id") is not None
            else note_data.get("note_id"),
            fields=json.loads(note_data["fields"])
            if isinstance(note_data["fields"], str)
            else note_data["fields"],
            tags=json.loads(note_data["tags"])
            if isinstance(note_data["tags"], str)
            else note_data["tags"],
            note_type_id=NotetypeId(int(note_data["note_type_id"])),
            anki_did=local_did,
            protected_fields=protected_fields,
            protected_tags=protected_tags,
        )
        dids_for_note = set(c.did for c in note.cards())
        dids = dids | dids_for_note

    if first_time_import:
        local_did = _cleanup_first_time_deck_import(dids, local_did)

    return local_did


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


def _cleanup_first_time_deck_import(
    dids_cards_were_imported_to: Iterable[DeckId], created_did: DeckId
) -> Optional[DeckId]:
    dids = set(dids_cards_were_imported_to)

    # remove "Custom Study" decks from dids
    dids = {did for did in dids if not mw.col.decks.is_filtered(did)}

    # if there is a single deck where all the existing cards were before the import,
    # move the new cards there (from the newly created deck) and remove the created deck
    # takes subdecks into account
    if (dids_wh_created := dids - set([created_did])) and (
        (common_ancestor_did := lowest_level_common_ancestor_did(dids_wh_created))
    ) is not None:
        cids = mw.col.find_cards(f"deck:{mw.col.decks.name(created_did)}")
        mw.col.set_deck(cids, common_ancestor_did)
        LOGGER.debug(f"Moved new cards to common ancestor deck {common_ancestor_did=}")

        mw.col.decks.remove([created_did])
        LOGGER.debug(f"Removed created deck {created_did=}")
        return common_ancestor_did

    return created_did


def update_or_create_note(
    anki_id: NoteId,
    ankihub_id: str,
    fields: List[Dict],
    tags: List[str],
    note_type_id: NotetypeId,
    anki_did: DeckId,  # only relevant for newly created notes
    protected_fields: Dict[str, List[str]],
    protected_tags: List[str],
) -> Note:
    try:
        note = mw.col.get_note(id=anki_id)
        fields.append(
            {
                "name": constants.ANKIHUB_NOTE_TYPE_FIELD_NAME,
                # Put the AnkiHub field last
                "order": len(fields),
                "value": ankihub_id,
            }
        )
        prepare_note(note, ankihub_id, fields, tags, protected_fields, protected_tags)
        mw.col.update_note(note)
        LOGGER.debug(f"Updated note: {anki_id=}")
    except NotFoundError:
        note_type = mw.col.models.get(NotetypeId(note_type_id))
        note = mw.col.new_note(note_type)
        prepare_note(note, ankihub_id, fields, tags, protected_fields, protected_tags)
        note = create_note_with_id(note, anki_id, anki_did)
        LOGGER.debug(f"Created note: {anki_id=}")
    return note


def prepare_note(
    note: Note,
    ankihub_id: str,
    fields: List[Dict[str, Any]],
    tags: List[str],
    protected_fields: Dict[str, List[str]],
    protected_tags: List[str],
) -> None:
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)

    # update tags, but don't remove protected ones
    note.tags = list(set(note.tags).intersection(set(protected_tags)) | set(tags))

    # update fields which are not protected
    for field in fields:
        # TODO: won't work if note type name was changed, better use the note type id
        protected_fields_for_model = protected_fields.get(
            mw.col.models.get(note.mid)["name"], []
        )
        if field["name"] in protected_fields_for_model:
            continue

        note[field["name"]] = field["value"]


def fetch_remote_note_types_based_on_notes_data(
    notes_data: List[Dict],
) -> Dict[NotetypeId, NotetypeDict]:
    remote_mids = set(
        NotetypeId(int(note_dict["note_type_id"])) for note_dict in notes_data
    )
    result = fetch_remote_note_types(remote_mids)
    return result


def fetch_remote_note_types(
    mids: Iterable[NotetypeId],
) -> Dict[NotetypeId, NotetypeDict]:
    result = {}
    client = AnkiHubClient()
    for mid in mids:
        response = client.get_note_type(mid)

        if response.status_code != 200:
            LOGGER.debug(f"Failed fetching note type with id {mid}.")
            continue

        data = response.json()
        note_type = to_anki_note_type(data)
        result[mid] = note_type
    return result


def to_anki_note_type(note_type_data: Dict) -> NotetypeDict:
    """Turn JSON response from AnkiHubClient.get_note_type into NotetypeDict."""
    del note_type_data["anki_id"]
    note_type_data["tmpls"] = note_type_data.pop("templates")
    note_type_data["flds"] = note_type_data.pop("fields")
    return note_type_data


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

    note_types_with_field_conflicts: List[Tuple[NotetypeDict, NotetypeDict]] = []
    for mid, remote_note_type in remote_note_types.items():
        local_note_type = mw.col.models.get(mid)

        def field_tuples(note_type: NotetypeDict) -> List[Tuple[int, str]]:
            return [(field["ord"], field["name"]) for field in note_type["flds"]]

        if not field_tuples(local_note_type) == field_tuples(remote_note_type):
            LOGGER.debug(
                f'Fields of local note type "{local_note_type["name"]}" differ from remote note_type. '
                f"local:\n{pformat(field_tuples(local_note_type))}\nremote:\n{pformat(field_tuples(remote_note_type))}"
            )
            note_types_with_field_conflicts.append((local_note_type, remote_note_type))

    for local_note_type, remote_note_type in note_types_with_field_conflicts:
        local_note_type["flds"] = remote_note_type["flds"]
        mw.col.models.update_dict(local_note_type)
        LOGGER.debug(f"Updated fields of note type {local_note_type}.")


def reset_note_types_of_notes_based_on_notes_data(notes_data: List[Dict]) -> None:
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""
    nid_mid_pairs = [
        (NoteId(int(note_dict["anki_id"])), NotetypeId(int(note_dict["note_type_id"])))
        for note_dict in notes_data
    ]
    reset_note_types_of_notes(nid_mid_pairs)


def sync_with_progress() -> None:
    def on_done(future: Future):
        # Don't raise exception when attempting to sync with AnkiHub
        # without an Internet connection.
        if exc := future.exception():
            if not isinstance(exc, (ConnectionError, HTTPError)):
                LOGGER.debug(f"Unable to sync:\n{exc}")
                report_exception()
                raise exc
            else:
                LOGGER.debug("Skipping sync due to no Internet connection.")
                tooltip("AnkiHub: No Internet connection. Skipping sync.")
        else:
            mw.reset()

    if config.private_config.token:
        mw.taskman.with_progress(
            sync_with_ankihub,
            label="Synchronizing with AnkiHub",
            on_done=on_done,
            parent=mw,
            immediate=True,
        )
    else:
        LOGGER.debug("Skipping sync due to no token.")


def setup_sync_on_startup() -> None:
    def on_profile_open():
        # syncing with AnkiHub during sync with AnkiWeb causes an error,
        # this is why we have to wait until the AnkiWeb sync is done if there is one
        if not mw.can_auto_sync():
            sync_with_progress()
        else:

            def on_sync_did_finish():
                sync_with_progress()
                gui_hooks.sync_did_finish.remove(on_sync_did_finish)

            gui_hooks.sync_did_finish.append(on_sync_did_finish)

    gui_hooks.profile_did_open.append(on_profile_open)
