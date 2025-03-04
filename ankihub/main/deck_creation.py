"""Code for creating an AnkiHub deck from an existing deck in Anki."""

import typing
import uuid
from dataclasses import dataclass
from typing import Dict, List

import aqt
from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import NoteId

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client.models import NoteInfo
from ..db import ankihub_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .exporting import to_note_data
from .subdecks import add_subdeck_tags_to_notes
from .utils import (
    change_note_types_of_notes,
    create_backup,
    get_note_types_in_deck,
    modified_ankihub_note_type_name,
    modified_note_type,
)


@dataclass
class DeckCreationResult:
    ankihub_did: uuid.UUID
    notes_data: List[NoteInfo]


def create_ankihub_deck(
    deck_name: str,
    private: bool,
    add_subdeck_tags: bool = False,
) -> DeckCreationResult:
    LOGGER.info(
        "Creating AnkiHub deck...",
        deck_name=deck_name,
        private=private,
        add_subdeck_tags=add_subdeck_tags,
    )

    create_backup()

    aqt.mw.col.models._clear_cache()

    deck_id = aqt.mw.col.decks.id(deck_name)
    note_ids = list(map(NoteId, aqt.mw.col.find_notes(f'deck:"{deck_name}"')))

    old_to_new_mid_dict = _create_note_types_for_deck(deck_id)
    _change_note_types_of_notes(note_ids, old_to_new_mid_dict)

    if add_subdeck_tags:
        add_subdeck_tags_to_notes(anki_deck_name=deck_name, ankihub_deck_name=deck_name)

    nids = aqt.mw.col.find_notes(f'deck:"{deck_name}"')
    notes_data = [
        to_note_data(aqt.mw.col.get_note(nid), set_new_id=True) for nid in nids
    ]

    _set_ankihub_id_fields_based_on_notes_data(notes_data)

    ankihub_did = _upload_deck(
        deck_id,
        notes_data=notes_data,
        private=private,
    )

    # Add note types to AnkiHub DB
    for new_mid in old_to_new_mid_dict.values():
        note_type = aqt.mw.col.models.get(new_mid)
        ankihub_db.upsert_note_type(ankihub_did=ankihub_did, note_type=note_type)

    # Add notes to AnkiHub DB
    ankihub_db.upsert_notes_data(ankihub_did=ankihub_did, notes_data=notes_data)
    ankihub_db.update_mod_values_based_on_anki_db(notes_data=notes_data)

    return DeckCreationResult(ankihub_did=ankihub_did, notes_data=notes_data)


def _create_note_types_for_deck(deck_id: DeckId) -> Dict[NotetypeId, NotetypeId]:
    """Create note types for the deck and return a mapping from old note type IDs to new note type IDs."""
    result: Dict[NotetypeId, NotetypeId] = {}
    model_ids = get_note_types_in_deck(deck_id)
    for mid in model_ids:
        new_model = modified_note_type(aqt.mw.col.models.get(mid))
        new_model["name"] = modified_ankihub_note_type_name(
            new_model["name"], aqt.mw.col.decks.name(deck_id)
        )
        aqt.mw.col.models.ensure_name_unique(new_model)
        new_model["id"] = 0
        aqt.mw.col.models.add_dict(new_model)
        result[mid] = aqt.mw.col.models.by_name(new_model["name"])["id"]
    return result


def _change_note_types_of_notes(
    note_ids: typing.List[NoteId], note_type_mapping: dict
) -> None:
    LOGGER.info("Changing note types of notes...", note_type_mapping=note_type_mapping)
    nid_mid_pairs = []
    for note_id in note_ids:
        note = aqt.mw.col.get_note(id=note_id)
        target_note_type_id = note_type_mapping[note.mid]
        nid_mid_pairs.append((note_id, target_note_type_id))

    change_note_types_of_notes(nid_mid_pairs=nid_mid_pairs)
    LOGGER.info("Changed note types of notes.")


def _set_ankihub_id_fields_based_on_notes_data(notes_data: List[NoteInfo]) -> None:
    """Assign UUID to notes that have an AnkiHub ID field already."""
    updated_notes = []
    LOGGER.info("Assigning AnkiHub IDs to notes...")
    for note_data in notes_data:
        note = aqt.mw.col.get_note(id=NoteId(note_data.anki_nid))
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(note_data.ah_nid)
        updated_notes.append(note)
    aqt.mw.col.update_notes(updated_notes)
    LOGGER.info("Updated notes.", updated_notes_count=len(updated_notes))


def _upload_deck(
    did: DeckId,
    notes_data: List[NoteInfo],
    private: bool,
) -> uuid.UUID:
    """Upload the deck to AnkiHub."""

    deck_name = aqt.mw.col.decks.name(did)

    note_types_data = [
        aqt.mw.col.models.get(mid) for mid in get_note_types_in_deck(did)
    ]

    client = AnkiHubClient()
    ankihub_did = client.upload_deck(
        deck_name=deck_name,
        notes_data=notes_data,
        note_types_data=note_types_data,
        anki_deck_id=did,
        private=private,
    )

    return ankihub_did
