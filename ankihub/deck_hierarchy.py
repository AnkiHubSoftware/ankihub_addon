import re
import uuid
from typing import List, Optional

from anki.errors import NotFoundError
from anki.notes import Note
from aqt import mw

from . import LOGGER
from .db import ankihub_db
from .settings import config
from .utils import nids_in_deck_but_not_in_subdeck

DECK_HIERARCHY_TAG_PREFIX = "AH_Deck_Hierarchy"


def build_deck_hierarchy_and_move_cards_into_it(
    ankihub_did: uuid.UUID, notes: Optional[List[Note]] = None
) -> None:
    """Move cards belonging to the ankihub deck into their subdeck based on the deck hierarchy tags of the notes.
    If notes is None, all of the cards belonging to the deck will be moved.
    If notes is not None, only the cards belonging to the provided notes will be moved.
    Creates subdecks if they don't exist.
    Subdecks that are empty after the move will be deleted.
    """

    if notes is None:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ankihub_did)
        notes = [mw.col.get_note(nid) for nid in nids]

    root_deck_id = config.deck_config(ankihub_did).anki_id
    # the deck name name could be different from the deck name in the config
    root_deck_name = mw.col.decks.name(root_deck_id)

    # create missing subdecks
    hierarchy_tags = set(
        tag for note in notes if (tag := hierarchy_tag(note.tags)) is not None
    )
    deck_names = [
        hierarchy_tag_to_deck_name(root_deck_name, tag) for tag in hierarchy_tags
    ]
    create_decks(deck_names)

    # move cards into subdecks
    for note in notes:
        if (hierarchy_tag_ := hierarchy_tag(note.tags)) is None:
            set_deck_while_respecting_odid(note, root_deck_id)
        else:
            deck_name = hierarchy_tag_to_deck_name(root_deck_name, hierarchy_tag_)
            deck_id = mw.col.decks.id(deck_name, create=False)
            for card in note.cards():
                # if the card is in a filtered deck, we only change the original deck id
                if card.odid == 0:
                    card.did = deck_id
                else:
                    card.odid = deck_id
                card.flush()

    # remove empty subdecks
    for name_and_did in mw.col.decks.children(root_deck_id):
        _, did = name_and_did
        # The card count includes cards in subdecks and in filtered decks.
        # This is good, as we don't want to delete a deck which is an original deck of cards which are
        # currently in filtered decks.
        try:
            if mw.col.decks.card_count(did, include_subdecks=True) == 0:
                mw.col.decks.remove([did])
                LOGGER.debug(f"Removed empty subdeck with id {did}.")
        except NotFoundError:
            # this can happen if a parent deck was deleted earlier in the loop
            pass

    LOGGER.info("Built deck hierarchy and moved cards into it.")


def hierarchy_tag_to_deck_name(top_level_deck_name: str, tag: str) -> str:
    return f"{top_level_deck_name}::{tag.split('::', 1)[1]}"


def create_decks(deck_names: List[str]) -> None:
    for deck_name in deck_names:
        mw.col.decks.add_normal_deck_with_name(deck_name)


def hierarchy_tag(tags: List[str]) -> str:
    result = next(
        (tag for tag in tags if tag.startswith(DECK_HIERARCHY_TAG_PREFIX)),
        None,
    )
    return result


def flatten_hierarchy(ankihub_did: uuid.UUID) -> None:
    raise NotImplementedError


def add_deck_hierarchy_tags_to_notes(deck_name: str, separator: str) -> None:
    """To every note in the deck a tags is added that indicates where in the deck hierarchy
    the note is located. For example, if the deck is called "A" and the note is in
    the deck "A::B::C", the tag f"{DECK_HIERARCHY_TAG_PREFIX}::B::C" is added to the note.
    If the note is in deck "A" and not in a subdeck of A no tag is added.
    """

    assert "::" not in deck_name, "Deck must be a top level deck."

    LOGGER.debug("Adding subdeck tags to notes.")

    deck = mw.col.decks.by_name(deck_name)

    # add tags to notes in subdecks
    # (mw.col.decks also returns children of children)
    for child_deck_name, _ in mw.col.decks.children(deck["id"]):
        child_deck_name_wh_root = child_deck_name.split("::", 1)[1]
        tag = subdeck_name_to_tag(child_deck_name_wh_root, separator)
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
