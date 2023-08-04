"""Code for resetting notes to the state they have in the AnkiHub database.
(This is the state that the notes had on AnkiHub when the add-on synced with AnkiHub last time.)
"""
import uuid
from typing import Sequence

from anki.notes import NoteId

from ..db import ankihub_db
from ..settings import config
from .importing import AnkiHubImporter


def reset_local_changes_to_notes(
    nids: Sequence[NoteId],
    ah_did: uuid.UUID,
) -> None:
    # all notes have to be from the ankihub deck with the given uuid

    deck_config = config.deck_config(ah_did)

    importer = AnkiHubImporter()

    # import deck with empty notes_data to reset changes to note types and deck
    # this is needed so that notes_data can be retrieved from the database if the fields
    # of the note type have changed
    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes_data=[],
        deck_name=deck_config.name,
        local_did=deck_config.anki_id,
        is_first_import_of_deck=False,
    )

    notes_data = [
        note_data
        for nid in nids
        if (note_data := ankihub_db.note_data(nid)) is not None
    ]

    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes_data=notes_data,
        deck_name=deck_config.name,
        is_first_import_of_deck=False,
        local_did=deck_config.anki_id,
        # we don't move existing notes between decks here, users might not want that
        subdecks_for_new_notes_only=deck_config.subdecks_enabled,
    )

    # this way the notes won't be marked as "changed after sync" anymore
    ankihub_db.reset_mod_values_in_anki_db(list(nids))
