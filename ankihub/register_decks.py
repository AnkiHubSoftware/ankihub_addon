"""Logic for the initial steps of registering local decks with collaborative
decks for deck creators.
"""
import os
import typing
import uuid
from copy import deepcopy
from typing import Dict

from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt import mw

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .db import ankihub_db
from .exporting import to_note_data
from .settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, config
from .utils import (
    change_note_type_of_note,
    create_backup,
    get_note_types_in_deck,
    modify_note_type,
)

DIR_PATH = os.path.dirname(os.path.abspath(__file__))


def upload_deck(did: DeckId, private: bool) -> str:
    """Upload the deck to AnkiHub."""

    deck_name = mw.col.decks.name(did)
    nids = mw.col.find_notes(f'deck:"{deck_name}"')

    notes_data = [to_note_data(mw.col.get_note(nid)) for nid in nids]

    note_types_data = [mw.col.models.get(mid) for mid in get_note_types_in_deck(did)]

    client = AnkiHubClient()
    ankihub_did = str(
        client.upload_deck(
            deck_name=deck_name,
            notes_data=notes_data,
            note_types_data=note_types_data,
            anki_deck_id=did,
            private=private,
        )
    )
    return ankihub_did


def create_collaborative_deck(deck_name: str, private: bool) -> str:
    LOGGER.debug("Creating collaborative deck")

    create_backup()

    mw.col.models._clear_cache()

    deck_id = mw.col.decks.id(deck_name)
    note_ids = list(map(NoteId, mw.col.find_notes(f'deck:"{deck_name}"')))

    note_type_mapping = create_note_types_for_deck(deck_id)
    change_note_types_of_notes(note_ids, note_type_mapping)

    assign_ankihub_ids(note_ids)

    ankihub_did = upload_deck(deck_id, private=private)
    ankihub_db.save_notes_from_nids(
        ankihub_did=ankihub_did,
        nids=note_ids,
    )
    return ankihub_did


def create_note_types_for_deck(deck_id: DeckId) -> Dict[NotetypeId, NotetypeId]:
    result: Dict[NotetypeId, NotetypeId] = {}
    model_ids = get_note_types_in_deck(deck_id)
    for mid in model_ids:
        new_model = deepcopy(mw.col.models.get(mid))
        modify_note_type(new_model)
        name = f"{new_model['name']} ({mw.col.decks.name(deck_id)} / {config.private_config.user})"
        new_model["name"] = name
        mw.col.models.ensure_name_unique(new_model)
        new_model["id"] = 0
        mw.col.models.add_dict(new_model)
        result[mid] = mw.col.models.by_name(new_model["name"])["id"]
    return result


def change_note_types_of_notes(
    note_ids: typing.List[NoteId], note_type_mapping: dict
) -> None:
    LOGGER.debug(
        f"Changing note types of notes according to mapping: {note_type_mapping}"
    )
    for note_id in note_ids:
        note = mw.col.get_note(id=note_id)
        target_note_type_id = note_type_mapping[note.mid]
        change_note_type_of_note(note_id, target_note_type_id)
    LOGGER.debug("Changed note types of notes.")


def assign_ankihub_ids(note_ids: typing.List[NoteId]) -> None:
    """Assign UUID to notes that have an AnkiHub ID field already."""
    updated_notes = []
    LOGGER.debug("Assigning AnkiHub IDs to notes.")
    for note_id in note_ids:
        note = mw.col.get_note(id=note_id)
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(uuid.uuid4())
        updated_notes.append(note)
    mw.col.update_notes(updated_notes)
    LOGGER.debug(f"Updated notes: {', '.join(map(str, note_ids))}")
