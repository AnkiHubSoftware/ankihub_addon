"""Code for handling notes that were deleted on the webapp.
This is a temporary solution for selected notes from the AnKing deck."""

import json
import uuid
from pathlib import Path
from typing import List

import aqt
from anki.utils import ids2str

from .. import LOGGER
from ..db import ankihub_db
from ..main.utils import is_tag_in_list
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME

# tag for notes which were deleted from the webapp
TAG_FOR_DELETED_NOTES = "AnkiHub_Deleted"

DELETED_NOTES_FILE = (
    Path(__file__).parent.parent / "resources/deleted_notes_from_anking_deck.json"
)


def handle_notes_deleted_from_webapp() -> None:
    """Handle notes which were deleted from the webapp from the AnKing deck.
    This is a temporary solution just for these notes. In the future, we should
    handle note deletion during sync."""
    ah_nids_strings = json.loads(DELETED_NOTES_FILE.read_text())["ankihub_note_ids"]
    ah_nids = [uuid.UUID(ah_nid_string) for ah_nid_string in ah_nids_strings]
    _mark_notes_in_anki_and_delete_from_db(ah_nids)


def _mark_notes_in_anki_and_delete_from_db(ah_nids: List[uuid.UUID]) -> None:
    """Clear ankihub id fields and add a special tag for the notes in Anki.
    Delete the notes from the AnkiHub DB.
    We don't delete the notes from the Anki collection because we don't want to
    delete notes that people have studied."""

    anki_nids = [x for x in ankihub_db.ankihub_nids_to_anki_nids(ah_nids).values() if x]
    anki_nids_which_exist_in_anki_db = aqt.mw.col.db.list(
        f"SELECT id FROM notes WHERE id IN {ids2str(anki_nids)}"
    )

    if not anki_nids_which_exist_in_anki_db:
        LOGGER.info("No notes to delete.")
        return

    notes = []
    for anki_nid in anki_nids_which_exist_in_anki_db:
        note = aqt.mw.col.get_note(anki_nid)

        if ANKIHUB_NOTE_TYPE_FIELD_NAME in note:
            note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = ""

        if not is_tag_in_list(TAG_FOR_DELETED_NOTES, note.tags):
            note.tags.append(TAG_FOR_DELETED_NOTES)

        notes.append(note)

    aqt.mw.col.update_notes(notes)
    LOGGER.info("Marked notes as deleted in Anki collection.")

    ankihub_db.remove_notes(ah_nids)
    LOGGER.info("Deleted notes from AnkiHub DB.")
