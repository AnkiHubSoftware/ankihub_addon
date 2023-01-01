import re
import uuid
from typing import List, Optional

from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.notes import Note, NoteId
from aqt import mw

from . import LOGGER
from .db import ankihub_db
from .settings import config
from .utils import nids_in_deck_but_not_in_subdeck

# root tag for tags that indicate which subdeck a note belongs to
SUBDECK_TAG = "AnkiHub_Subdeck"


def build_subdecks_and_move_cards_to_them(
    ankihub_did: uuid.UUID, nids: Optional[List[NoteId]] = None
) -> None:
    """Move cards belonging to the ankihub deck into their subdeck based on the subdeck tags of the notes.
    If notes is None, all of the cards belonging to the deck will be moved.
    If notes is not None, only the cards belonging to the provided notes will be moved.
    Creates subdecks if they don't exist.
    Subdecks that are empty after the move will be deleted.
    """

    if nids is None:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ankihub_did)

    notes = [mw.col.get_note(nid) for nid in nids]

    root_deck_id = config.deck_config(ankihub_did).anki_id
    # the deck name name could be different from the deck name in the config
    root_deck_name = mw.col.decks.name(root_deck_id)

    # create missing subdecks
    subdeck_tags = set(
        tag for note in notes if (tag := subdeck_tag(note.tags)) is not None
    )
    deck_names = [subdeck_tag_to_deck_name(root_deck_name, tag) for tag in subdeck_tags]
    create_decks(deck_names)

    # move cards into subdecks
    for note in notes:
        if (subdeck_tag_ := subdeck_tag(note.tags)) is None:
            set_deck_while_respecting_odid(note, root_deck_id)
        else:
            deck_name = subdeck_tag_to_deck_name(root_deck_name, subdeck_tag_)
            deck_id = mw.col.decks.id(deck_name, create=False)
            set_deck_while_respecting_odid(note, deck_id)

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

    LOGGER.info("Built subdecks and moved cards to them.")


def set_deck_while_respecting_odid(note: Note, deck_id: DeckId) -> None:
    for card in note.cards():
        # if the card is in a filtered deck, we only change the original deck id
        if card.odid == 0:
            card.did = deck_id
        else:
            card.odid = deck_id
        card.flush()


def subdeck_tag_to_deck_name(top_level_deck_name: str, tag: str) -> str:
    return f"{top_level_deck_name}::{tag.split('::', 1)[1]}"


def create_decks(deck_names: List[str]) -> None:
    for deck_name in deck_names:
        mw.col.decks.add_normal_deck_with_name(deck_name)


def subdeck_tag(tags: List[str]) -> Optional[str]:
    result = next(
        (tag for tag in tags if tag.startswith(SUBDECK_TAG)),
        None,
    )
    return result


def flatten_deck(ankihub_did: uuid.UUID) -> None:
    """Remove all subdecks of the deck with the given ankihub_did and move all cards
    that were in the subdecks back to the root deck."""

    # move cards that are in subdecks back to the root deck
    root_deck_id = config.deck_config(ankihub_did).anki_id
    root_deck_name = mw.col.decks.name(root_deck_id)
    nids = mw.col.find_notes(f"deck:{root_deck_name}::*")
    for nid in nids:
        note = mw.col.get_note(nid)
        set_deck_while_respecting_odid(note, root_deck_id)

    # remove subdecks
    for name_and_did in mw.col.decks.children(root_deck_id):
        _, did = name_and_did
        try:
            mw.col.decks.remove([did])
            LOGGER.debug(f"Removed subdeck with id {did}.")
        except NotFoundError:
            # this can happen if a parent deck was deleted earlier in the loop
            pass


def add_subdeck_tags_notes(deck_name: str, separator: str) -> None:
    """To every note in the deck a tags is added that indicates in which subdeck
    the note is located. For example, if the deck is called "A" and the note is in
    the deck "A::B::C", the tag f"{TAG_FOR_SUBDECK}::B::C" is added to the note.
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

    # Replace spaces with separator (dashes) to avoid making multiple tags
    result = result.replace(" ", separator)

    # Remove duplicate separators
    result = re.sub(f"{separator}+", separator, result)

    return result
