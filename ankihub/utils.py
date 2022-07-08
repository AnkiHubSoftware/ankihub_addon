import json
import re
import time
from concurrent.futures import Future
from pathlib import Path
from pprint import pformat
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError

import anki
import aqt
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import ChangeNotetypeRequest, NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import checksum, ids2str
from aqt import mw
from aqt.importing import AnkiPackageImporter
from aqt.utils import tr
from requests.exceptions import ConnectionError

from . import LOGGER, constants
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config
from .constants import (
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ANKIHUB_NOTE_TYPE_MODIFICATION_STRING,
    URL_VIEW_NOTE,
)


def note_type_contains_field(
    note_type: NoteType, field=constants.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return field in field_names


def get_note_types_in_deck(did: DeckId) -> List[NotetypeId]:
    """Returns list of note model ids in the given deck."""
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    dids_str = ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids_str} or odid in {dids_str}"
    )
    return mw.col.db.list(query)


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, _: aqt.editor.Editor
) -> str:
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
        return js
    extra = (
        'require("svelte/internal").tick().then(() => '
        "{{ require('anki/NoteEditor').instances[0].fields["
        "require('anki/NoteEditor').instances[0].fields.length -1"
        "].element.then((element) "
        "=> {{ element.hidden = true; }}); }});"
    )
    js += extra
    return js


def create_note_with_id(note: Note, anki_id: NoteId, anki_did: DeckId) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {anki_id=}")

    mw.col.add_note(note, DeckId(anki_did))

    # Swap out the note id that Anki assigns to the new note with our own id.
    mw.col.db.execute(f"UPDATE notes SET id={anki_id} WHERE id={note.id};")
    mw.col.db.execute(f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};")

    return mw.col.get_note(anki_id)


def prepare_note(
    note: Note, ankihub_id: str, fields: List[Dict], tags: List[str]
) -> None:
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    # TODO Make sure we don't update protected fields.
    for field in fields:
        note[field["name"]] = field["value"]


def update_or_create_note(
    anki_id: NoteId,
    ankihub_id: str,
    fields: List[Dict],
    tags: List[str],
    note_type_id: NotetypeId,
    anki_did: DeckId,  # only relevant for newly created notes
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
        prepare_note(note, ankihub_id, fields, tags)
        mw.col.update_note(note)
        LOGGER.debug(f"Updated note: {anki_id=}")
    except NotFoundError:
        note_type = mw.col.models.get(NotetypeId(note_type_id))
        note = mw.col.new_note(note_type)
        prepare_note(note, ankihub_id, fields, tags)
        note = create_note_with_id(note, anki_id, anki_did)
        LOGGER.debug(f"Created note: {anki_id=}")
    return note


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
                notes_data=notes_data,
                deck_name=deck["name"],
                local_did=deck["anki_id"],
            )
            config.save_latest_update(ankihub_did, data["latest_update"])
        else:
            LOGGER.debug(f"No new updates to synch for {ankihub_did=}")

    mw.reset()


def sync_on_profile_open() -> None:
    def on_done(future: Future):

        # Don't raise exception when automatically attempting to sync with AnkiHub
        # with no Internet connection.
        if exc := future.exception():
            LOGGER.debug(f"Unable to sync on profile open:\n{exc}")
            if not isinstance(exc, (ConnectionError, HTTPError)):
                raise exc

    if config.private_config.token:
        mw.taskman.with_progress(
            sync_with_ankihub,
            label="Synchronizing with AnkiHub",
            on_done=on_done,
            parent=mw,
        )


def adjust_note_types_based_on_notes_data(notes_data: List[Dict]) -> None:
    remote_mids = set(
        NotetypeId(int(note_dict["note_type_id"])) for note_dict in notes_data
    )
    remote_note_types = fetch_remote_note_types(remote_mids)
    adjust_note_types(remote_note_types)


def adjust_note_types(remote_note_types: Dict[NotetypeId, NotetypeDict]) -> None:
    # can be called when installing a deck for the first time and when synchronizing with AnkiHub

    LOGGER.debug("Beginning adjusting note types...")

    create_missing_note_types(remote_note_types)
    ensure_local_and_remote_fields_are_same(remote_note_types)
    modify_note_type_templates(remote_note_types.keys())

    LOGGER.debug("Adjusted note types.")


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


def reset_note_types_of_notes(nid_mid_pairs: List[Tuple[NoteId, NotetypeId]]) -> None:
    note_type_conflicts: Set[Tuple[NoteId, NotetypeId, NotetypeId]] = set()
    for nid, mid in nid_mid_pairs:
        try:
            note = mw.col.get_note(nid)
        except NotFoundError:
            # we don't care about missing notes here
            continue

        if note.mid != mid:
            note_type_conflicts.add((note.id, mid, note.mid))

    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        LOGGER.debug(
            f"Note types differ: anki_nid: {anki_nid} target_note_type_id {target_note_type_id}",
        )
        change_note_type_of_note(anki_nid, target_note_type_id)
        LOGGER.debug(
            f"Changed note type: anki_nid {anki_nid} target_note_type_id {target_note_type_id}",
        )


def change_note_type_of_note(nid: int, mid: int) -> None:
    current_schema: int = mw.col.db.scalar("select scm from col")
    note = mw.col.get_note(NoteId(nid))
    target_note_type = mw.col.models.get(NotetypeId(mid))
    request = ChangeNotetypeRequest(
        note_ids=[note.id],
        old_notetype_id=note.mid,
        new_notetype_id=NotetypeId(mid),
        current_schema=current_schema,
        new_fields=list(range(0, len(target_note_type["flds"]))),
    )
    mw.col.models.change_notetype_of_notes(request)


def create_note_type_with_id(note_type: NotetypeDict, mid: NotetypeId) -> None:
    note_type["id"] = 0
    changes = mw.col.models.add_dict(note_type)

    # Swap out the note type id that Anki assigns to the new note type with our own id.
    # TODO check if seperate statements are necessary
    mw.col.db.execute(f"UPDATE notetypes SET id={mid} WHERE id={changes.id};")
    mw.col.db.execute(f"UPDATE templates SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.db.execute(f"UPDATE fields SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.models._clear_cache()  # TODO check if this is necessary

    LOGGER.debug(f"Created note type: {mid}")
    LOGGER.debug(f"Note type:\n {pformat(note_type)}")


def to_anki_note_type(note_type_data: Dict) -> NotetypeDict:
    """Turn JSON response from AnkiHubClient.get_note_type into NotetypeDict."""
    del note_type_data["anki_id"]
    note_type_data["tmpls"] = note_type_data.pop("templates")
    note_type_data["flds"] = note_type_data.pop("fields")
    return note_type_data


def modify_note_type_templates(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = mw.col.models.get(mid)
        for template in note_type["tmpls"]:
            modify_template(template)
        mw.col.models.update_dict(note_type)


def modify_note_types(note_type_ids: Iterable[NotetypeId]) -> None:
    for mid in note_type_ids:
        note_type = mw.col.models.get(mid)
        modify_note_type(note_type)
        mw.col.models.update_dict(note_type)


def modify_note_type(note_type: NotetypeDict) -> None:
    """Adds the AnkiHub Field to the Note Type and modifies the template to
    display the field.
    """
    "Adds ankihub field. Adds link to ankihub in card template."
    LOGGER.debug(f"Modifying note type {note_type['name']}")

    modify_fields(note_type)

    templates = note_type["tmpls"]
    for template in templates:
        modify_template(template)


def modify_fields(note_type: Dict) -> None:
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names:
        LOGGER.debug(f"{constants.ANKIHUB_NOTE_TYPE_FIELD_NAME} already exists.")
        return
    ankihub_field = mw.col.models.new_field(ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # Put AnkiHub field last
    ankihub_field["ord"] = len(fields)
    note_type["flds"].append(ankihub_field)


def modify_template(template: Dict) -> None:
    ankihub_snippet = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        "<br><br>"
        f"\n{{{{#{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}\n"
        "<a class='ankihub' "
        f"href='{URL_VIEW_NOTE}{{{{{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}'>"
        "\nView Note on AnkiHub\n"
        "</a>"
        f"\n{{{{/{ANKIHUB_NOTE_TYPE_FIELD_NAME}}}}}\n"
        "<br>"
        f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    )

    snippet_pattern = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        r"[\w\W]*"
        f"<!-- END {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
    )

    if re.search(snippet_pattern, template["afmt"]):
        LOGGER.debug("Template modification was already present, updated it")
        template["afmt"] = re.sub(
            snippet_pattern,
            ankihub_snippet,
            template["afmt"],
        )
    else:
        template["afmt"] += "\n\n" + ankihub_snippet


def create_backup_with_progress() -> None:
    # has to be called from a background thread
    # if there is already a progress bar present this will not create a new one / modify the existing one

    LOGGER.debug("Starting backup...")
    mw.progress.start(label=tr.profiles_creating_backup())
    try:
        mw.col.create_backup(
            backup_folder=mw.pm.backupFolder(),
            force=True,
            wait_for_completion=True,
        )
        LOGGER.debug("Backup successful.")
    except Exception as exc:
        LOGGER.debug("Backup failed.")
        raise exc
    finally:
        mw.progress.finish()


def create_deck_with_id(deck_name: str, deck_id: DeckId) -> None:
    source_did = mw.col.decks.add_normal_deck_with_name(
        ensure_deck_name_unique(deck_name)
    ).id
    mw.col.db.execute(f"UPDATE decks SET id={deck_id} WHERE id={source_did};")
    mw.col.db.execute(f"UPDATE cards SET did={deck_id} WHERE did={source_did};")

    LOGGER.debug(f"Created deck {deck_name=} {deck_id=}")


def import_ankihub_deck(
    notes_data: List[dict],
    deck_name: str,  # name that will be used for a deck if a new one gets created
    local_did: DeckId = None,  # did that new notes should be put into if importing not for the first time
) -> DeckId:
    # Used for importing an ankihub deck and updates to an ankihub deck
    # When no local_did is provided this functions assumes that the deck gets installed for the first time
    # Returns id of the deck future cards should be imported into - the local_did

    LOGGER.debug(f"Importing ankihub deck {deck_name=} {local_did=}")

    dids: Set[DeckId] = set()  # set of ids of decks notes were imported into
    first_time_import = local_did is None

    local_did = adjust_deck(deck_name, local_did)
    adjust_note_types_based_on_notes_data(notes_data)
    reset_note_types_of_notes_based_on_notes_data(notes_data)

    # TODO fix differences between csv when installing for the first time vs. when updating
    # on the AnkiHub side
    # for example for one the fields name is "note_id" and for the other "id"
    for note_data in notes_data:
        LOGGER.debug(f"Trying to update or create note:\n {pformat(note_data)}")
        note = update_or_create_note(
            anki_id=NoteId(int((note_data["anki_id"]))),
            ankihub_id=note_data.get("id")
            if note_data.get("id") is not None
            else note_data.get("note_id"),
            fields=json.loads(note_data["fields"])
            if type(note_data["fields"]) == str
            else note_data["fields"],
            tags=json.loads(note_data["tags"])
            if type(note_data["tags"]) == str
            else note_data["tags"],
            note_type_id=NotetypeId(int(note_data["note_type_id"])),
            anki_did=local_did,
        )
        dids = dids.union(set(c.did for c in note.cards()))

    dids = {x for x in dids if not mw.col.decks.is_filtered(x)}

    if first_time_import:
        local_did = _cleanup_first_time_deck_import(dids, local_did)

    return local_did


def adjust_deck(deck_name: str, local_did: Optional[DeckId] = None) -> DeckId:
    if local_did is None:
        local_did = DeckId(
            mw.col.decks.add_normal_deck_with_name(
                ensure_deck_name_unique(deck_name)
            ).id
        )
        LOGGER.debug(f"Created deck {local_did=}")
    elif mw.col.decks.name_if_exists(local_did) is None:
        # recreate deck if it was deleted
        create_deck_with_id(ensure_deck_name_unique(deck_name), local_did)
        LOGGER.debug(f"Recreated deck {local_did=}")

    return local_did


def _cleanup_first_time_deck_import(
    dids_cards_were_imported_to: Iterable[DeckId], created_did: DeckId
) -> Optional[DeckId]:
    dids = set(dids_cards_were_imported_to)

    # if there is a single deck where all the existing notes were before the import,
    # move the new notes there (from the newly created deck) and remove the created deck
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


def lowest_level_common_ancestor_did(dids: Iterable[DeckId]) -> Optional[DeckId]:
    return mw.col.decks.id_for_name(
        lowest_level_common_ancestor_deck_name([mw.col.decks.name(did) for did in dids])
    )


def lowest_level_common_ancestor_deck_name(deck_names: Iterable[str]) -> Optional[str]:
    lowest_level_deck_name = max(deck_names, key=lambda name: name.count("::"))
    parts = lowest_level_deck_name.split("::")
    result = lowest_level_deck_name
    for i in range(1, len(parts) + 1):
        cur_deck_name = "::".join(parts[:i])
        if any(not name.startswith(cur_deck_name) for name in deck_names):
            result = "::".join(parts[: i - 1])
            break

    if result == "":
        return None
    else:
        return result


def ensure_deck_name_unique(deck_name: str) -> str:
    if not mw.col.decks.by_name(deck_name):
        return deck_name

    suffix = " (AnkiHub)"
    if suffix not in deck_name:
        deck_name += suffix
    else:
        deck_name += f" {checksum(str(time.time()))[:5]}"
    return deck_name


def install_deck_apkg(
    deck_file: Path,
    deck_name: str,
) -> DeckId:
    # Returns id of the deck future cards should be imported into.

    LOGGER.debug("Importing deck as apkg....")

    dids_before_import = all_dids()

    file = str(deck_file.absolute())
    importer = AnkiPackageImporter(mw.col, file)
    importer.run()

    new_dids = all_dids() - dids_before_import

    if new_dids:
        return highest_level_did(new_dids)
    else:
        # XXX: Ideally this function would check if all updated / skipped notes belong to one deck
        # and if this is the case not create a new deck (as it is done for csv imports).
        # To implement this we would need to know the decks that contain the cards which
        # were updated / skipped during the apkg import.
        return adjust_deck(deck_name)


def all_dids() -> Set[DeckId]:
    return {DeckId(x.id) for x in mw.col.decks.all_names_and_ids()}


def highest_level_did(dids: Iterable[DeckId]) -> DeckId:
    return min(dids, key=lambda did: mw.col.decks.name(did).count("::"))
