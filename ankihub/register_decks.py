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
from aqt import mw

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import NoteInfo
from .db import ankihub_db
from .exporting import to_note_data
from .settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, config
from .utils import (
    change_note_type_of_note,
    create_backup,
    get_note_types_in_deck,
    modify_note_type,
    nids_in_deck_but_not_in_subdeck,
)

DIR_PATH = os.path.dirname(os.path.abspath(__file__))

DECK_HIERARCHY_TAG_PREFIX = "AH_Deck_Hierarchy"


def upload_deck(did: DeckId, notes_data: List[NoteInfo], private: bool) -> uuid.UUID:
    """Upload the deck to AnkiHub."""

    deck_name = mw.col.decks.name(did)

    note_types_data = [mw.col.models.get(mid) for mid in get_note_types_in_deck(did)]

    client = AnkiHubClient()
    ankihub_did = client.upload_deck(
        deck_name=deck_name,
        notes_data=notes_data,
        note_types_data=note_types_data,
        anki_deck_id=did,
        private=private,
    )
    return ankihub_did


def create_collaborative_deck(
    deck_name: str, private: bool, add_deck_hierarchy_tags: bool = False
) -> uuid.UUID:
    LOGGER.debug("Creating collaborative deck")

    create_backup()

    mw.col.models._clear_cache()

    deck_id = mw.col.decks.id(deck_name)
    note_ids = list(map(NoteId, mw.col.find_notes(f'deck:"{deck_name}"')))

    note_type_mapping = create_note_types_for_deck(deck_id)
    change_note_types_of_notes(note_ids, note_type_mapping)

    if add_deck_hierarchy_tags:
        add_deck_hierarchy_tags_to_notes(deck_name, separator="_")

    nids = mw.col.find_notes(f'deck:"{deck_name}"')
    notes_data = [to_note_data(mw.col.get_note(nid), set_new_id=True) for nid in nids]

    set_ankihub_id_fields_based_on_notes_data(notes_data)

    ankihub_did = upload_deck(deck_id, notes_data=notes_data, private=private)
    ankihub_db.save_notes_data_and_mod_values(
        ankihub_did=ankihub_did, notes_data=notes_data
    )
    return ankihub_did


def create_note_types_for_deck(deck_id: DeckId) -> Dict[NotetypeId, NotetypeId]:
    result: Dict[NotetypeId, NotetypeId] = {}
    model_ids = get_note_types_in_deck(deck_id)
    for mid in model_ids:
        new_model = deepcopy(mw.col.models.get(mid))
        modify_note_type(new_model)
        name_without_modifications = note_type_name_without_ankihub_modifications(
            new_model["name"]
        )
        name = f"{name_without_modifications} ({mw.col.decks.name(deck_id)} / {config.user()})"
        new_model["name"] = name
        mw.col.models.ensure_name_unique(new_model)
        new_model["id"] = 0
        mw.col.models.add_dict(new_model)
        result[mid] = mw.col.models.by_name(new_model["name"])["id"]
    return result


def note_type_name_without_ankihub_modifications(name: str) -> str:
    return re.sub(r" \(.*? / .*?\)", "", name)


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


def set_ankihub_id_fields_based_on_notes_data(notes_data: List[NoteInfo]) -> None:
    """Assign UUID to notes that have an AnkiHub ID field already."""
    updated_notes = []
    LOGGER.debug("Assigning AnkiHub IDs to notes.")
    for note_data in notes_data:
        note = mw.col.get_note(id=NoteId(note_data.anki_nid))
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(note_data.ankihub_note_uuid)
        updated_notes.append(note)
    mw.col.update_notes(updated_notes)
    LOGGER.debug(f"Updated notes: {', '.join(map(str, [n.id for n in updated_notes]))}")


def add_deck_hierarchy_tags_to_notes(deck_name: str, separator: str) -> None:
    """To every note in the deck a tags is added that indicates where in the deck hierarchy
    the note is located. For example, if the deck is called "A" and the note is in
    the deck "A::B", the tag f"{DECK_HIERARCHY_TAG_PREFIX}::A::B" is added to the note."""

    assert "::" not in deck_name, "Deck must be a top level deck."

    LOGGER.debug("Adding subdeck tags to notes.")

    deck = mw.col.decks.by_name(deck_name)

    # add tags to notes in top level deck
    nids_in_top_level_deck = nids_in_deck_but_not_in_subdeck(deck_name)
    mw.col.tags.bulk_add(
        nids_in_top_level_deck, subdeck_name_to_tag(deck_name, separator)
    )

    # add tags to notes in subdecks
    # (mw.col.decks also returns children of children)
    for child_deck_name, _ in mw.col.decks.children(deck["id"]):
        tag = subdeck_name_to_tag(child_deck_name, separator)
        nids = nids_in_deck_but_not_in_subdeck(child_deck_name)
        mw.col.tags.bulk_add(nids, tag)


def subdeck_name_to_tag(deck_name: str, separator: str) -> str:
    """Convert deck name with spaces to compatible and clean Anki tag name starting with
    DECK_HIERARCHY_TAG_PREFIX."""

    result = f"{DECK_HIERARCHY_TAG_PREFIX}::{deck_name}"

    # Remove apostrophes
    result = result.replace("'", "")

    # Remove trailing spaces
    result = result.strip()
    result = re.sub(r" +::", "::", result)
    result = re.sub(r":: +", "::", result)

    # Remove spaces after commas
    result = result.replace(", ", ",")

    # Remove spaces around + signs
    result = result.replace(" +", "+")
    result = result.replace("+ ", "+")

    # Replace spaces with separator (dashes) to avoid making multiple tags
    result = result.replace(" ", separator)

    # Remove duplicate separators
    result = re.sub(f"{separator}+", separator, result)

    return result
