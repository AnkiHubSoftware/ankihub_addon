import re
import uuid
from typing import Dict, Iterable, List, Optional

from anki.decks import DeckId
from anki.errors import NotFoundError
from anki.notes import NoteId
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
    This function expects the home deck (the one of which the anki id is stored in the deck config) to exist.
    """

    if nids is None:
        nids = ankihub_db.anki_nids_for_ankihub_deck(ankihub_did)

    root_deck_id = config.deck_config(ankihub_did).anki_id
    if mw.col.decks.name_if_exists(root_deck_id) is None:
        raise NotFoundError(f"Deck with id {root_deck_id} not found")

    # the deck name name could be different from the deck name in the config
    root_deck_name = mw.col.decks.name(root_deck_id)

    # create mapping between notes and destination decks
    nid_to_dest_deck_name = _nid_to_destination_deck_name(
        nids, root_deck_name=root_deck_name
    )

    # create missing subdecks
    deck_names = set(nid_to_dest_deck_name.values())
    _create_decks(deck_names)

    # move cards to their destination decks
    for nid, dest_deck_name in nid_to_dest_deck_name.items():
        dest_did = mw.col.decks.id_for_name(dest_deck_name)
        _set_deck_while_respecting_odid(nid=nid, did=dest_did)

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


def _nid_to_destination_deck_name(
    nids: List[NoteId], root_deck_name: str
) -> Dict[NoteId, str]:
    result = dict()
    for nid in nids:
        tags_str = mw.col.db.scalar("SELECT tags FROM notes WHERE id = ?", nid)
        tags = mw.col.tags.split(tags_str)
        subdeck_tag_ = _subdeck_tag(tags)
        if subdeck_tag_ is None:
            deck_name = root_deck_name
        else:
            deck_name = _subdeck_tag_to_deck_name(root_deck_name, subdeck_tag_)
        result[nid] = deck_name
    return result


def _set_deck_while_respecting_odid(nid: NoteId, did: DeckId) -> None:
    """Moves the cards of the note to the deck. If a card is in a filtered deck
    it is not moved and only its original deck id value gets changed."""

    # using database operations for performance reasons
    cids = mw.col.db.list("SELECT id FROM cards WHERE nid = ?", nid)
    for cid in cids:
        odid = mw.col.db.scalar("SELECT odid FROM cards WHERE id=?", cid)
        # if the card is in a filtered deck, we only change the original deck id
        if odid == 0:
            # setting usn to -1 so that this change is synced to AnkiWeb
            # see https://github.com/ankidroid/Anki-Android/wiki/Database-Structure#cards
            mw.col.db.execute("UPDATE cards SET did=?, usn=-1 WHERE id=?", did, cid)
        else:
            mw.col.db.execute("UPDATE cards SET odid=?, usn=-1 WHERE id=?", did, cid)


def _subdeck_tag_to_deck_name(top_level_deck_name: str, tag: str) -> str:
    return f"{top_level_deck_name}::{tag.split('::', 1)[1]}"


def _create_decks(deck_names: Iterable[str]) -> None:
    for deck_name in deck_names:
        mw.col.decks.add_normal_deck_with_name(deck_name)


def _subdeck_tag(tags: List[str]) -> Optional[str]:
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
    nids = mw.col.find_notes(f'"deck:{root_deck_name}::*"')
    for nid in nids:
        _set_deck_while_respecting_odid(nid, root_deck_id)

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
        tag = _subdeck_name_to_tag(child_deck_name_wh_root, separator)
        nids = nids_in_deck_but_not_in_subdeck(child_deck_name)
        mw.col.tags.bulk_add(nids, tag)


def _subdeck_name_to_tag(deck_name: str, separator: str) -> str:
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
