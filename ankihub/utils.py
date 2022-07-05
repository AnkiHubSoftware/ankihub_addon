import re
from concurrent.futures import Future
from pprint import pformat
from typing import Dict, Iterable, List, Set, Tuple
from urllib.error import HTTPError

import anki
import aqt
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import ChangeNotetypeRequest, NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from anki.utils import ids2str
from aqt import mw
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


def create_note_with_id(note: Note, anki_id: int, anki_did: int) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {anki_id=}")

    mw.col.add_note(note, DeckId(anki_did))

    # Swap out the note id that Anki assigns to the new note with our own id.
    mw.col.db.execute(f"UPDATE notes SET id={anki_id} WHERE id={note.id};")
    mw.col.db.execute(f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};")

    return note


def prepare_note(
    note: Note, ankihub_id: int, fields: List[Dict], tags: List[str]
) -> None:
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    # TODO Make sure we don't update protected fields.
    for field in fields:
        note[field["name"]] = field["value"]


def update_or_create_note(
    anki_id: int,
    ankihub_id: int,
    fields: List[Dict],
    tags: List[str],
    note_type_id: int,
    ankihub_deck_id: str = None,
) -> Note:
    try:
        note = mw.col.get_note(id=NoteId(anki_id))
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
        anki_did = config.private_config.decks[ankihub_deck_id].get(
            "anki_id", 1
        )  # XXX if the deck doesn't exist, use the default deck
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
    data = None
    for deck in decks:
        collected_notes = []
        for response in client.get_deck_updates(
            deck, since=config.private_config.last_sync
        ):
            if response.status_code != 200:
                return

            data = response.json()
            notes = data["notes"]
            if notes:
                collected_notes += notes

        if collected_notes:

            adjust_note_types_based_on_notes_data(collected_notes)
            reset_note_types_of_notes_based_on_notes_data(collected_notes)

            for note in collected_notes:
                (
                    deck_id,
                    ankihub_id,
                    tags,
                    anki_id,
                    fields,
                    note_type,
                    note_type_id,
                ) = note.values()
                LOGGER.debug(f"Trying to update or create note:\n {pformat(note)}")
                update_or_create_note(
                    anki_id,
                    ankihub_id,
                    fields,
                    tags,
                    int(note_type_id),
                    deck_id,
                )
                # Should last sync be tracked separately for each deck?
                mw.reset()
    if data:
        config.save_last_sync(time=data["latest_update"])


def sync_on_profile_open() -> None:
    def on_done(future: Future):

        # Don't raise exception when automatically attempting to sync with AnkiHub
        # with no Internet connection.
        if exc := future.exception():
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
            raise Exception(f"Failed fetching note type with id: {mid}.")

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
    if not missings_mids:
        return

    LOGGER.debug(f"Missing note types: {missings_mids}")

    for mid in missings_mids:
        new_note_type = remote_note_types[mid]
        create_note_type_with_id(new_note_type, mid)

    LOGGER.debug("Created missing note types.")


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

    if not note_types_with_field_conflicts:
        return

    for local_note_type, remote_note_type in note_types_with_field_conflicts:
        local_note_type["flds"] = remote_note_type["flds"]
        mw.col.models.update_dict(local_note_type)

    LOGGER.debug("Updated fields of local note types.")


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

    if not note_type_conflicts:
        return

    LOGGER.debug(
        f"Note types of local notes differ from remote note types: {note_type_conflicts}",
    )

    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        change_note_type_of_note(anki_nid, target_note_type_id)

    LOGGER.debug("Reset note types of local notes.")


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
    data = note_type_data
    del data["anki_id"]
    data["tmpls"] = data.pop("templates")
    data["flds"] = data.pop("fields")
    return data


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
        LOGGER.debug("Template modifcation was already present, updated it")
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
