import re
from aqt import mw
from . import LOGGER
from .utils import nids_in_deck_but_not_in_subdeck
DECK_HIERARCHY_TAG_PREFIX = "AH_Deck_Hierarchy"


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
