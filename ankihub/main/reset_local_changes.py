"""Code for resetting notes to the state they have in the AnkiHub database.
(This is the state that the notes had on AnkiHub when the add-on synced with AnkiHub last time.)
"""
import uuid
from typing import Sequence

from anki.notes import NoteId

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db
from ..main.note_types import fetch_note_types_based_on_notes_in_db
from ..settings import config
from .importing import AnkiHubImporter


def reset_local_changes_to_notes(
    nids: Sequence[NoteId],
    ah_did: uuid.UUID,
) -> None:
    # all notes have to be from the ankihub deck with the given uuid

    deck_config = config.deck_config(ah_did)

    importer = AnkiHubImporter()

    # Import deck with empty notes_data to reset changes to note types and deck.
    # This is needed so that notes_data can be retrieved from the database if the fields
    # of the note type have changed
    # TODO This won't be needed when note types will be stored in the AnkiHub database.
    client = AnkiHubClient()
    protected_fields = client.get_protected_fields(ah_did=ah_did)
    protected_tags = client.get_protected_tags(ah_did=ah_did)
    note_types = fetch_note_types_based_on_notes_in_db(ankihub_did=ah_did)
    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes=[],
        note_types=note_types,
        deck_name=deck_config.name,
        anki_did=deck_config.anki_id,
        is_first_import_of_deck=False,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
    )

    notes_data = [
        note_data
        for nid in nids
        if (note_data := ankihub_db.note_data(nid)) is not None
    ]

    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes=notes_data,
        note_types=note_types,
        deck_name=deck_config.name,
        is_first_import_of_deck=False,
        anki_did=deck_config.anki_id,
        protected_fields=protected_fields,
        protected_tags=protected_tags,
        # we don't move existing notes between decks here, users might not want that
        subdecks_for_new_notes_only=deck_config.subdecks_enabled,
    )

    # this way the notes won't be marked as "changed after sync" anymore
    ankihub_db.reset_mod_values_in_anki_db(list(nids))
