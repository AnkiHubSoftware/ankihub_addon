"""Logic for the initial steps of registering local decks with collaborative
decks for deck creators.
"""
import os
import re
import typing
import uuid
from copy import deepcopy
from typing import Dict, List

from anki.decks import DeckId
from anki.models import NotetypeId
from anki.notes import NoteId
import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import NoteInfo
from .db import ankihub_db
from .exporting import to_note_data
from .settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, config
from .subdecks import add_subdeck_tags_to_notes
from .utils import (
    change_note_type_of_note,
    create_backup,
    get_note_types_in_deck,
    modify_note_type,
)

DIR_PATH = os.path.dirname(os.path.abspath(__file__))


def upload_deck(did: DeckId, notes_data: List[NoteInfo], private: bool) -> uuid.UUID:
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

    # Upload all existing local assets for this deck
    # (assets that are referenced on Deck's notes)
    aqt.mw.taskman.run_in_background(
        task=client.upload_assets_for_deck,
        args={"ah_did": ankihub_did, "notes_data": notes_data},
        on_done=lambda future: LOGGER.info(
            f"Finished uploading assets for deck {ankihub_did}"
        ),
    )
    # client.upload_assets_for_deck(ankihub_did, notes_data)

    return ankihub_did


def create_collaborative_deck(
    deck_name: str, private: bool, add_subdeck_tags: bool = False
) -> uuid.UUID:
    LOGGER.info("Creating collaborative deck")

    create_backup()

    aqt.mw.col.models._clear_cache()

    deck_id = aqt.mw.col.decks.id(deck_name)
    note_ids = list(map(NoteId, aqt.mw.col.find_notes(f'deck:"{deck_name}"')))

    note_type_mapping = create_note_types_for_deck(deck_id)
    change_note_types_of_notes(note_ids, note_type_mapping)

    if add_subdeck_tags:
        add_subdeck_tags_to_notes(anki_deck_name=deck_name, ankihub_deck_name=deck_name)

    nids = aqt.mw.col.find_notes(f'deck:"{deck_name}"')
    notes_data = [
        to_note_data(aqt.mw.col.get_note(nid), set_new_id=True) for nid in nids
    ]

    set_ankihub_id_fields_based_on_notes_data(notes_data)

    ankihub_did = upload_deck(deck_id, notes_data=notes_data, private=private)
    ankihub_db.upsert_notes_data(ankihub_did=ankihub_did, notes_data=notes_data)
    ankihub_db.transfer_mod_values_from_anki_db(notes_data=notes_data)
    return ankihub_did


def create_note_types_for_deck(deck_id: DeckId) -> Dict[NotetypeId, NotetypeId]:
    result: Dict[NotetypeId, NotetypeId] = {}
    model_ids = get_note_types_in_deck(deck_id)
    for mid in model_ids:
        new_model = deepcopy(aqt.mw.col.models.get(mid))
        modify_note_type(new_model)
        name_without_modifications = note_type_name_without_ankihub_modifications(
            new_model["name"]
        )
        name = f"{name_without_modifications} ({aqt.mw.col.decks.name(deck_id)} / {config.user()})"
        new_model["name"] = name
        aqt.mw.col.models.ensure_name_unique(new_model)
        new_model["id"] = 0
        aqt.mw.col.models.add_dict(new_model)
        result[mid] = aqt.mw.col.models.by_name(new_model["name"])["id"]
    return result


def note_type_name_without_ankihub_modifications(name: str) -> str:
    return re.sub(r" \(.*? / .*?\)", "", name)


def change_note_types_of_notes(
    note_ids: typing.List[NoteId], note_type_mapping: dict
) -> None:
    LOGGER.info(
        f"Changing note types of notes according to mapping: {note_type_mapping}"
    )
    for note_id in note_ids:
        note = aqt.mw.col.get_note(id=note_id)
        target_note_type_id = note_type_mapping[note.mid]
        change_note_type_of_note(note_id, target_note_type_id)
    LOGGER.info("Changed note types of notes.")


def set_ankihub_id_fields_based_on_notes_data(notes_data: List[NoteInfo]) -> None:
    """Assign UUID to notes that have an AnkiHub ID field already."""
    updated_notes = []
    LOGGER.info("Assigning AnkiHub IDs to notes.")
    for note_data in notes_data:
        note = aqt.mw.col.get_note(id=NoteId(note_data.anki_nid))
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(note_data.ankihub_note_uuid)
        updated_notes.append(note)
    aqt.mw.col.update_notes(updated_notes)
    LOGGER.info(f"Updated notes: {', '.join(map(str, [n.id for n in updated_notes]))}")
