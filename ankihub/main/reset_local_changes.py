"""Code for resetting notes to the state they have in the AnkiHub database.
(This is the state that the notes had on AnkiHub when the add-on synced with AnkiHub last time.)
"""
import uuid
from typing import Sequence

from anki.models import NotetypeId
from anki.notes import NoteId

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db
from ..settings import SuspendNewCardsOfExistingNotes, config
from .importing import AnkiHubImporter


def reset_local_changes_to_notes(
    nids: Sequence[NoteId],
    ah_did: uuid.UUID,
) -> None:
    # all notes have to be from the ankihub deck with the given uuid

    deck_config = config.deck_config(ah_did)

    client = AnkiHubClient()
    protected_fields = client.get_protected_fields(ah_did=ah_did)
    protected_tags = client.get_protected_tags(ah_did=ah_did)

    notes_data = [
        note_data
        for nid in nids
        if (note_data := ankihub_db.note_data(nid)) is not None
    ]
    note_types = {
        mid: ankihub_db.note_type_dict(note_type_id=mid)
        for mid in ankihub_db.note_types_for_ankihub_deck(ah_did)
    }

    importer = AnkiHubImporter()
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
        suspend_new_cards_of_new_notes=deck_config.suspend_new_cards_of_new_notes,
        suspend_new_cards_of_existing_notes=deck_config.suspend_new_cards_of_existing_notes,
    )

    # this way the notes won't be marked as "changed after sync" anymore
    ankihub_db.reset_mod_values_in_anki_db(list(nids))

    LOGGER.info(f"Local changes to notes {nids} have been reset.")


def reset_local_changes_to_note_type(
    mid: NotetypeId,
) -> None:
    """Resets the local changes to the note type with the given id to the state it has in the AnkiHub database."""

    # TODO Not sure how to handle the note type being in multiple decks yet.
    ah_dids = ankihub_db.ankihub_dids_for_note_type(mid)

    ah_did = list(ah_dids)[0]

    # TODO This doesn't reset template changes yet.
    deck_config = config.deck_config(ah_did)
    importer = AnkiHubImporter()
    importer.import_ankihub_deck(
        ankihub_did=ah_did,
        notes=[],
        note_types={mid: ankihub_db.note_type_dict(note_type_id=mid)},
        protected_fields={},
        protected_tags=[],
        deck_name=deck_config.name,
        is_first_import_of_deck=False,
        suspend_new_cards_of_new_notes=False,
        suspend_new_cards_of_existing_notes=SuspendNewCardsOfExistingNotes.NEVER,
    )

    LOGGER.info(f"Local changes to note type {mid} have been reset.")
