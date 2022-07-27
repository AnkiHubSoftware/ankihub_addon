"""Logic for the initial steps of registering local decks with collaborative
decks for deck creators.
"""
import os
import pathlib
import re
import tempfile
import typing
import uuid
from copy import deepcopy
from typing import Dict

from anki.decks import DeckId
from anki.exporting import AnkiPackageExporter
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt import mw
from requests import Response

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config
from .constants import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .db import AnkiHubDB
from .utils import (
    change_note_type_of_note,
    create_backup_with_progress,
    get_note_types_in_deck,
    modify_note_type,
)

DIR_PATH = os.path.dirname(os.path.abspath(__file__))


def upload_deck(did: DeckId) -> Response:
    """Upload the deck to AnkiHub."""

    deck_name = mw.col.decks.name(did)
    exporter = AnkiPackageExporter(mw.col)
    exporter.did = did
    exporter.includeMedia = False
    exporter.includeTags = True
    deck_uuid = uuid.uuid4()
    out_dir = pathlib.Path(tempfile.mkdtemp())
    deck_name = re.sub('[\\\\/?<>:*|"^]', "_", deck_name)
    out_file = out_dir / f"{deck_name}-{deck_uuid}.apkg"
    exporter.exportInto(str(out_file))
    LOGGER.debug(f"Deck {deck_name} exported to {out_file}")
    mw.col.models._clear_cache()
    client = AnkiHubClient()
    response = client.upload_deck(file=out_file, anki_deck_id=did)
    return response


def create_collaborative_deck(deck_name: str) -> Response:
    LOGGER.debug("Creating collaborative deck")

    create_backup_with_progress()

    mw.col.models._clear_cache()

    deck_id = mw.col.decks.id(deck_name)
    note_ids = list(map(NoteId, mw.col.find_notes(f'deck:"{deck_name}"')))

    note_type_mapping = create_note_types_for_deck(deck_id)
    change_note_types_of_notes(note_ids, note_type_mapping)

    assign_ankihub_ids(note_ids)

    response = upload_deck(deck_id)
    if response.status_code != 201:
        return response

    ankihub_did = response.json()["deck_id"]
    db = AnkiHubDB()
    db.save_notes_from_nids(
        ankihub_did=ankihub_did,
        nids=note_ids,
    )

    return response


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
    LOGGER.debug(f"Assigning AnkiHub IDs to {', '.join(map(str, note_ids))}")
    for note_id in note_ids:
        note = mw.col.get_note(id=note_id)
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(uuid.uuid4())
        updated_notes.append(note)
    mw.col.update_notes(updated_notes)
    LOGGER.debug(f"Updated notes: {', '.join(map(str, note_ids))}")
