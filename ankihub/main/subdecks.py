"""This module contains code for adding subdeck-tags to notes that indicate which subdeck they belong to.
AnkiHub does not store subdeck information in the database and subdeck-tags are used to store this information instead.
Then there is code to create decks based on these tags and move cards to the correct subdeck.
It also contains code to flatten the subdecks of a deck and move all cards to the root deck in order to
revert the changes made by moving cards to their subdecks.
"""

import re
import uuid
from typing import Dict, Iterable, List, Optional

import aqt
from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.notes import NoteId

from .. import LOGGER
from ..db import ankihub_db
from ..db.db import NOTE_NOT_DELETED_CONDITION
from ..db.models import AnkiHubNote
from ..settings import config
from .block_exam_subdecks import get_exam_subdecks
from .utils import move_notes_to_decks_while_respecting_odid, nids_in_deck_but_not_in_subdeck, note_ids_in_decks

# root tag for tags that indicate which subdeck a note belongs to
SUBDECK_TAG = "AnkiHub_Subdeck"


def deck_contains_subdeck_tags(ah_did: uuid.UUID) -> bool:
    """Return whether the given deck contains any notes which have subdeck tags in the AnkiHub database."""
    return AnkiHubNote.filter(
        NOTE_NOT_DELETED_CONDITION,
        ankihub_deck_id=ah_did,
        tags__ilike=f"%{SUBDECK_TAG}::%::%",
    ).exists()


def build_subdecks_and_move_cards_to_them(ankihub_did: uuid.UUID, nids: Optional[List[NoteId]] = None) -> None:
    """Move cards belonging to the ankihub deck into their subdeck based on the subdeck tags of the notes.
    If nids is None, all of the cards belonging to the deck will be moved.
    If nids is not None, only the cards belonging to the provided notes will be moved.
    Creates subdecks if they don't exist.
    Non-filtered subdecks that are empty after the move will be deleted.
    This function expects the home deck (the one of which the anki id is stored in the deck config) to exist.
    """

    if nids is None:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ankihub_did)

    root_deck_id = config.deck_config(ankihub_did).anki_id

    # the anki root deck name name can be different from the ankihub deck name in the config
    anki_root_deck_name = aqt.mw.col.decks.name(root_deck_id)

    # create mapping between notes and destination decks
    nid_to_dest_deck_name = _nid_to_destination_deck_name(nids, anki_root_deck_name=anki_root_deck_name)

    # create missing subdecks
    deck_names = set(nid_to_dest_deck_name.values())
    _create_decks(deck_names)

    # move cards to their destination decks
    nid_to_did = {nid: aqt.mw.col.decks.id_for_name(deck_name) for nid, deck_name in nid_to_dest_deck_name.items()}
    move_notes_to_decks_while_respecting_odid(nid_to_did=nid_to_did)

    # Remove empty unprotected subdecks
    for name, deck_id in _get_subdecks_excluding_protected(root_deck_id):
        try:
            is_empty = aqt.mw.col.decks.card_count(deck_id, include_subdecks=True) == 0
        except NotFoundError:
            # This can happen if a parent deck was deleted earlier in the loop
            LOGGER.debug(f"Deck {deck_id} not found during removal process")
            continue

        if is_empty:
            # Remove the empty deck
            aqt.mw.col.decks.remove([deck_id])
            LOGGER.info("Removed empty subdeck", did=deck_id, name=name)

    LOGGER.info("Built subdecks and moved cards to them.")


def _nid_to_destination_deck_name(nids: List[NoteId], anki_root_deck_name: str) -> Dict[NoteId, str]:
    result = dict()
    missing_nids = []
    for nid in nids:
        tags_str = aqt.mw.col.db.scalar("SELECT tags FROM notes WHERE id = ?", nid)
        if not tags_str:
            # When this query returns None, that means that the note does not exist in the Anki database.
            # (Notes without tags have an empty string in the tags field.)
            # In this case we ignore the note.
            missing_nids.append(nid)
            continue

        tags = aqt.mw.col.tags.split(tags_str)
        subdeck_tag_ = _subdeck_tag(tags)
        if subdeck_tag_ is None:
            # If the note does not have a subdeck tag, we don't move it.
            # This is to avoid moving notes that don't have subdeck tags from the subdecks to the root deck.
            # Users were unhappy about this behavior as their notes were moved to the root deck when
            # they enabled the subdeck feature and they had to use a backup to restore their notes to their
            # original subdecks.
            continue
        else:
            deck_name = _subdeck_tag_to_deck_name(anki_root_deck_name, subdeck_tag_)
            if deck_name is None:
                # ignore invalid subdeck tag
                continue
        result[nid] = deck_name

    if missing_nids:
        LOGGER.warning("Notes are not in the Anki database.", missing_nids=missing_nids)

    return result


def _subdeck_tag_to_deck_name(anki_root_deck_name: str, tag: str) -> Optional[str]:
    """The tag should be of the form "AnkiHub_Subdeck::ankihub_deck_name[::subdeck_name]*"
    and this returns "anki_root_deck_name[::subdeck_name]*" in this case.
    If the tag has less than 2 parts the tag is invalid and this returns None."""

    if "::" not in tag:
        return None

    if tag.count("::") == 1:
        return anki_root_deck_name
    else:
        _, _, subdeck_name = tag.split("::", maxsplit=2)
        return f"{anki_root_deck_name}::{subdeck_name}"


def _create_decks(deck_names: Iterable[str]) -> None:
    for deck_name in deck_names:
        aqt.mw.col.decks.add_normal_deck_with_name(deck_name)


def _subdeck_tag(tags: List[str]) -> Optional[str]:
    result = next(
        (tag for tag in tags if tag.lower().startswith(SUBDECK_TAG.lower())),
        None,
    )
    return result


def flatten_deck(ankihub_did: uuid.UUID) -> None:
    """Flatten the deck hierarchy for the given ankihub_did.

    This function:
    1. Moves all cards from non-protected subdecks to the root deck
    2. Removes all non-protected subdecks

    Protected decks are excluded from this operation:
    - Block exam subdecks and their ancestors/descendants
    - Filtered decks and their ancestors/descendants
    """
    # Get the root deck ID
    root_deck_id = config.deck_config(ankihub_did).anki_id

    # Get all child decks except protected decks (exam subdecks, filtered decks, and their ancestors/descendants)
    decks_to_flatten = _get_subdecks_excluding_protected(root_deck_id)
    deck_ids_to_flatten: list[DeckId] = [deck_id for _, deck_id in decks_to_flatten]

    # Get note IDs from the decks we want to flatten
    nids = note_ids_in_decks(deck_ids_to_flatten, include_filtered=True)
    nid_to_did = {nid: root_deck_id for nid in nids}
    move_notes_to_decks_while_respecting_odid(nid_to_did=nid_to_did)

    # Remove unprotected subdecks
    if deck_ids_to_flatten:
        aqt.mw.col.decks.remove(deck_ids_to_flatten)
        LOGGER.info(f"Removed {len(deck_ids_to_flatten)} subdeck(s)")


def _get_subdecks_excluding_protected(root_deck_id: DeckId) -> list[tuple[str, DeckId]]:
    """Get all child decks under root_deck_id, excluding protected decks and their descendants.

    Protected decks include:
    - Block exam subdecks
    - Filtered decks
    - All ancestors and descendants of the above

    Returns a list of (name, id) tuples.
    """

    # Get all child decks
    child_decks = aqt.mw.col.decks.children(root_deck_id)

    # Build set of core protected deck names (exam subdecks and filtered decks)
    exam_subdeck_names = {exam_name for exam_name, _ in get_exam_subdecks(root_deck_id)}
    filtered_deck_names = {deck_name for deck_name, deck_id in child_decks if aqt.mw.col.decks.is_filtered(deck_id)}
    core_protected_names = exam_subdeck_names | filtered_deck_names

    def is_protected(deck_name: str) -> bool:
        """Check if deck is protected (core, ancestor, or descendant of core)."""
        return (
            deck_name in core_protected_names
            or any(deck_name.startswith(f"{core_name}::") for core_name in core_protected_names)
            or any(core_name.startswith(f"{deck_name}::") for core_name in core_protected_names)
        )

    return [(deck_name, deck_id) for deck_name, deck_id in child_decks if not is_protected(deck_name)]


def add_subdeck_tags_to_notes(anki_deck_name: str, ankihub_deck_name: str) -> None:
    """To every note in the deck a tag is added that indicates in which subdeck
    the note is located. For example, if the deck is called "A" and the note is in
    the deck "A::B::C", the tag f"{TAG_FOR_SUBDECK}::A::B::C" is added to the note.
    If the note is in deck "A" and not in a subdeck of A then f"{TAG_FOR_SUBDECK}::A"
    is added to the note.

    The ankihub_deck_name is used to replace the root deck name in the subdeck tags.
    We can't just use the root of the anki_deck_name, because the
    deck name can be different from the ankihub deck name, as the user can change the
    name of the deck in Anki, but the subdeck tag should always be based on the ankihub
    deck name.
    """

    assert "::" not in anki_deck_name, "Deck must be a top level deck."

    LOGGER.info(
        "Adding subdeck tags to notes.",
        anki_deck_name=anki_deck_name,
        ankihub_deck_name=ankihub_deck_name,
    )

    deck = aqt.mw.col.decks.by_name(anki_deck_name)

    # add tags to notes in root deck
    nids = nids_in_deck_but_not_in_subdeck(anki_deck_name)
    tag = _subdeck_name_to_tag(ankihub_deck_name)
    aqt.mw.col.tags.bulk_add(nids, tag)

    # add tags to notes in subdecks
    # (aqt.mw.col.decks also returns children of children)
    for child_deck_name, _ in aqt.mw.col.decks.children(deck["id"]):
        deck_name_wh_root = child_deck_name.split("::", maxsplit=1)[1]
        deck_name_with_ankihub_root = f"{ankihub_deck_name}::{deck_name_wh_root}"
        tag = _subdeck_name_to_tag(deck_name_with_ankihub_root)
        nids = nids_in_deck_but_not_in_subdeck(child_deck_name)
        aqt.mw.col.tags.bulk_add(nids, tag)


def _subdeck_name_to_tag(deck_name: str) -> str:
    """Convert deck name with spaces to compatible and clean Anki tag name starting with
    TAG_FOR_SUBDECK."""

    result = f"{SUBDECK_TAG}::{deck_name}"

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

    # Replace spaces with dashes to avoid making multiple tags
    result = result.replace(" ", "_")

    # Remove duplicate separators
    result = re.sub("_+", "_", result)

    return result
