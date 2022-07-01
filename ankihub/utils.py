import re
import time
from pprint import pformat
from typing import Dict, List, Set, Tuple
from urllib.error import HTTPError

import anki
import aqt
from anki import notetypes_pb2, utils
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NoteType, NotetypeDict, NotetypeId
from anki.notes import Note, NoteId
from aqt import mw
from aqt.utils import askUser
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
    dids_str = utils.ids2str(dids)
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


def create_note_with_id(note_type_id: int, anki_id: int, anki_did: int) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    LOGGER.debug(f"Trying to create note: {anki_id=} {note_type_id=}")

    note_type = mw.col.models.get(NotetypeId(note_type_id))
    note = Note(col=mw.col, model=note_type)
    mw.col.add_note(note, DeckId(anki_did))

    # Swap out the note id that Anki assigns to the new note with our own id.
    sql = (
        f"UPDATE notes SET id={anki_id} WHERE id={note.id};"
        f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};"
    )
    mw.col.db.execute(sql)

    LOGGER.debug(f"Created note: {anki_id}")
    return note


def update_note(
    note: Note, anki_id: int, ankihub_id: int, fields: List[Dict], tags: List[str]
):
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    # TODO Make sure we don't update protected fields.
    for field in fields:
        note[field["name"]] = field["value"]
    LOGGER.debug(f"Updated note {anki_id}")


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
        update_note(note, anki_id, ankihub_id, fields, tags)
        mw.col.update_note(note)
    except NotFoundError:
        anki_did = config.private_config.decks[ankihub_deck_id].get(
            "anki_id", 1
        )  # XXX if the deck doesn't exist, use the default deck
        note = create_note_with_id(note_type_id, anki_id, anki_did)
        LOGGER.debug(f"Created note {anki_id}")

        update_note(note, anki_id, ankihub_id, fields, tags)
    return note


def sync_with_ankihub():
    LOGGER.debug("Trying to sync with AnkiHub.")
    client = AnkiHubClient()
    decks = config.private_config.decks
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

            mw._create_backup_with_progress(user_initiated=False)
            adjust_note_types(collected_notes)

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
                    anki_id, ankihub_id, fields, tags, int(note_type_id), deck_id
                )
                # Should last sync be tracked separately for each deck?
                mw.reset()
                config.save_last_sync(time=data["latest_update"])


def sync_on_profile_open():
    if config.private_config.token:
        try:
            # Don't raise exception when automatically attempting to sync with AnkiHub
            # with no Internet connection.
            sync_with_ankihub()
        except (ConnectionError, HTTPError):
            pass


def adjust_note_types(
    notes_data: List[Dict],
) -> None:

    fetch_missing_note_types(notes_data)

    # TODO make sure all managed local note types have the same fields as the remote note types

    reset_note_types_of_notes(notes_data)


def reset_note_types_of_notes(notes_data: List[Dict]):
    """Set the note type of notes back to the note type they have in the remote deck if they have a different one"""

    note_type_conflicts: Set[Tuple[NoteId, NotetypeId, NotetypeId]] = set()
    for note_dict in notes_data:
        anki_nid = NoteId(int(note_dict["anki_id"]))
        note_type_id = NotetypeId(int(note_dict["note_type_id"]))

        try:
            note = mw.col.get_note(anki_nid)
        except Exception:
            continue

        if note.mid != note_type_id:
            note_type_conflicts.add((note.id, note_type_id, note.mid))

    if not note_type_conflicts:
        return

    LOGGER.debug(
        f"Note types of local notes differ from remote note types: {note_type_conflicts}",
    )

    if not askUser(
        "Note types of some AnkiHub managed notes were changed. If you continue, they will be changed back.\n"
        "When you press Yes, Anki will ask you to confirm a full sync with AnkiWeb on the next sync.\n"
        "Continue synchronization with AnkiHub?"
    ):
        return

    if not mw.confirm_schema_modification():
        return

    for anki_nid, target_note_type_id, _ in note_type_conflicts:
        change_note_type_of_note(anki_nid, target_note_type_id)


def change_note_type_of_note(nid: int, mid: int):
    current_schema: int = mw.col.db.scalar("select scm from col")
    note = mw.col.get_note(NoteId(nid))
    target_note_type = mw.col.models.get(NotetypeId(mid))
    request = notetypes_pb2.ChangeNotetypeRequest(
        note_ids=[note.id],
        old_notetype_id=note.mid,
        new_notetype_id=NotetypeId(mid),
        current_schema=current_schema,
        new_fields=list(range(0, len(target_note_type["flds"]))),
    )
    mw.col.models.change_notetype_of_notes(request)


def fetch_missing_note_types(notes_data: List[Dict]):
    missing_note_types_ids = set(
        [
            mid
            for note_dict in notes_data
            if mw.col.models.get((mid := NotetypeId(int(note_dict["note_type_id"]))))
            is None
        ]
    )
    LOGGER.debug(f"Missing note types: {missing_note_types_ids}")

    client = AnkiHubClient()
    for mid in missing_note_types_ids:
        response = client.get_note_type(mid)

        if response.status_code != 200:
            return

        data = response.json()
        note_type = to_anki_note_type(data)

        modify_note_type(note_type)
        create_note_type_with_id(note_type, mid)


def create_note_type_with_id(mid, note_type):
    changes = mw.col.models.add_dict(note_type)

    # Swap out the note type id that Anki assigns to the new note type with our own id.
    # TODO check if seperate statements are necessary
    mw.col.db.execute(f"UPDATE notetypes SET id={mid} WHERE id={changes.id};")
    mw.col.db.execute(f"UPDATE templates SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.db.execute(f"UPDATE fields SET ntid={mid} WHERE ntid={changes.id};")
    mw.col.models._clear_cache()  # TODO check if this is necessary

    LOGGER.debug(f"Created note type: {mid}")


def to_anki_note_type(note_type_data: Dict) -> NotetypeDict:
    data = note_type_data
    del data["anki_id"]
    d = {
        "id": 0,
        "type": 0,  # XXX hardcoded
        "mod": int(time.time()),
        "usn": -1,
        "sortf": 0,
        "did": 0,
        "css": "",
        "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage{amssymb,amsmath}"
        + "\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n",
        "latexPost": "\\end{document}",
        "latexsvg": False,
        "req": [0, "any", [0]],
        "vers": [],
        "tags": [],
    }
    data.update(d)

    data["tmpls"] = []
    for template in data["templates"]:
        template["bfont"] = "Arial"
        template["bsize"] = 12

        data["tmpls"].append(template)
    del data["templates"]

    data["flds"] = []
    for field in data["fields"]:
        field["ord"] = field["order"]
        del field["order"]

        field["sticky"] = False
        field["rtl"] = False
        field["font"] = "Arial"
        field["size"] = 18
        field["media"] = []

        data["flds"].append(field)
    del data["fields"]

    return data


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


def modify_template(template: Dict):
    ankihub_snippet = (
        f"<!-- BEGIN {ANKIHUB_NOTE_TYPE_MODIFICATION_STRING} -->"
        "<br>"
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
        template["afmt"] += ankihub_snippet
