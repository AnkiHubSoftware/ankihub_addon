from typing import Dict, List

import anki
import aqt
from anki import utils
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.models import NoteType, NotetypeId
from anki.notes import Note, NoteId
from aqt import mw

from . import LOGGER, constants
from .ankihub_client import AnkiHubClient
from .config import Config


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


def create_note_with_id(note_type, anki_id) -> Note:
    """Create a new note, add it to the appropriate deck and override the note id with
    the note id of the original note creator."""
    note_type = mw.col.models.by_name(note_type)
    note = Note(col=mw.col, model=note_type)
    # TODO Add to an appropriate deck.
    mw.col.add_note(note, DeckId(1))
    # Swap out the note id that Anki assigns to the new note with our own id.
    sql = (
        f"UPDATE notes SET id={anki_id} WHERE id={note.id};"
        f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};"
    )
    mw.col.db.execute(sql)
    LOGGER.debug(f"Created note: {anki_id}")
    return note


def update_note(note, anki_id, ankihub_id, fields, tags):
    note[constants.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    # TODO Make sure we don't update protected fields.
    for field in fields:
        note[field["name"]] = field["value"]
    LOGGER.debug(f"Updated note {anki_id}")


def update_or_create_note(anki_id, ankihub_id, fields, tags, note_type) -> Note:
    try:
        note = mw.col.get_note(id=NoteId(anki_id))
        fields.update(
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
        note = create_note_with_id(note_type, anki_id)
        LOGGER.debug(f"Created note {anki_id}")
        update_note(note, anki_id, ankihub_id, fields, tags)
    return note


def sync_with_ankihub():
    client = AnkiHubClient()
    config = Config()
    decks = config.private_config.decks
    for deck in decks:
        collected_notes = []
        for response in client.get_deck_updates(deck):
            if response.status_code == 200:
                data = response.json()
                notes = data["notes"]
                if notes:
                    collected_notes += notes

        if collected_notes:
            mw._create_backup_with_progress(user_initiated=False)
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
                update_or_create_note(anki_id, ankihub_id, fields, tags, note_type)
                # Should last sync be tracked separately for each deck?
                mw.reset()
                config.save_last_sync(time=data["latest_update"])


def sync_on_profile_open():
    config = Config()
    if config.private_config.token:
        sync_with_ankihub()
