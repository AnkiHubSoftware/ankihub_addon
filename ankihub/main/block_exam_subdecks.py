"""Functions for managing block exam subdecks."""

import uuid
from datetime import date, datetime
from typing import List, Optional, Tuple

import aqt
from anki.decks import DeckId
from anki.notes import NoteId
from anki.utils import ids2str
from aqt.operations.scheduling import unsuspend_cards

from .. import LOGGER
from ..settings import BlockExamSubdeckConfig, config
from .utils import move_notes_to_decks_while_respecting_odid, note_ids_in_deck_hierarchy


def create_block_exam_subdeck(
    ankihub_deck_id: uuid.UUID, subdeck_name: str, due_date: Optional[str] = None
) -> Tuple[str, bool]:
    """Create a new block exam subdeck.

    Returns:
        Tuple of (actual_subdeck_name, was_renamed)
        was_renamed is True if the name was modified to avoid conflicts
    """
    deck_config = config.deck_config(ankihub_deck_id)
    if not deck_config:
        raise ValueError("Deck config not found")

    anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
    if not anki_deck_name:
        raise ValueError("Parent deck not found")

    # Check for conflicts and generate unique name if needed
    full_subdeck_name = f"{anki_deck_name}::{subdeck_name}"
    original_name = subdeck_name
    counter = 1

    while aqt.mw.col.decks.by_name(full_subdeck_name) is not None:
        subdeck_name = f"{original_name} ({counter})"
        full_subdeck_name = f"{anki_deck_name}::{subdeck_name}"
        counter += 1

    # Create the subdeck
    subdeck_id = DeckId(aqt.mw.col.decks.add_normal_deck_with_name(full_subdeck_name).id)

    # Make the subdeck inherit the parent deck's option group
    subdeck = aqt.mw.col.decks.get(subdeck_id)
    main_deck = aqt.mw.col.decks.get(deck_config.anki_id)
    subdeck["conf"] = main_deck["conf"]
    aqt.mw.col.decks.update(subdeck)

    # Save configuration if due date provided
    if due_date:
        config_item = BlockExamSubdeckConfig(ankihub_deck_id=ankihub_deck_id, subdeck_id=subdeck_id, due_date=due_date)
        config.upsert_block_exam_subdeck(config_item)

    LOGGER.info("Created block exam subdeck", subdeck_name=subdeck_name, due_date=due_date)

    return subdeck_name, subdeck_name != original_name


def add_notes_to_block_exam_subdeck(
    ankihub_deck_id: uuid.UUID,
    subdeck_name: str,
    note_ids: List[NoteId],
    due_date: Optional[str] = None,
    unsuspend_notes: bool = False,
) -> int:
    """Add notes to a block exam subdeck and update configuration.

    Args:
        ankihub_deck_id: The AnkiHub deck ID
        subdeck_name: Name of the subdeck (without parent deck prefix)
        note_ids: List of note IDs to add to the subdeck
        due_date: Due date for the subdeck in YYYY-MM-DD format
        unsuspend_notes: Whether to unsuspend the notes after adding them

    Returns:
        The number of notes actually moved to the subdeck (excluding notes already in the subdeck).
    """
    if not note_ids:
        return 0

    deck_config = config.deck_config(ankihub_deck_id)
    if not deck_config:
        raise ValueError("Deck config not found")

    anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
    if not anki_deck_name:
        raise ValueError("Parent deck not found")

    full_subdeck_name = f"{anki_deck_name}::{subdeck_name}"
    subdeck = aqt.mw.col.decks.by_name(full_subdeck_name)
    if not subdeck:
        raise ValueError(f"Subdeck {full_subdeck_name} not found")

    subdeck_id = subdeck["id"]

    # Find which notes are NOT already in the subdeck.
    # A note is considered "not in subdeck" if it has at least one card not in that subdeck
    query = f"""
        SELECT DISTINCT nid
        FROM cards
        WHERE nid IN {ids2str(note_ids)}
        AND did != {subdeck_id} AND odid != {subdeck_id}
    """
    notes_to_move = aqt.mw.col.db.list(query)

    # Move only the notes that aren't already in the subdeck
    if notes_to_move:
        move_notes_to_decks_while_respecting_odid({nid: subdeck_id for nid in notes_to_move})

    # Unsuspend notes if requested
    if unsuspend_notes:
        # Get card IDs for provided notes that are currently in the subdeck
        card_ids = aqt.mw.col.db.list(f"SELECT id FROM cards WHERE nid IN {ids2str(note_ids)} AND did = {subdeck_id}")
        if card_ids:
            unsuspend_cards(parent=aqt.mw, card_ids=card_ids).run_in_background()

    # Update configuration with due date
    if due_date:
        config_item = BlockExamSubdeckConfig(
            ankihub_deck_id=ankihub_deck_id, subdeck_id=subdeck["id"], due_date=due_date
        )
        config.upsert_block_exam_subdeck(config_item)

    LOGGER.info(
        "Added notes to block exam subdeck",
        subdeck_name=subdeck_name,
        requested_count=len(note_ids),
        actually_moved=len(notes_to_move),
        due_date=due_date,
        unsuspend_notes=unsuspend_notes,
    )

    return len(notes_to_move)


def check_block_exam_subdeck_due_dates() -> List[BlockExamSubdeckConfig]:
    """Check for block exam subdecks that are past their due date.

    Returns:
        List of expired subdeck configurations
    """
    expired_subdecks = []
    today = date.today()

    for subdeck_config in config.get_block_exam_subdecks():
        if not subdeck_config.due_date:
            continue
        due_date = datetime.strptime(subdeck_config.due_date, "%Y-%m-%d").date()

        if today >= due_date:
            expired_subdecks.append(subdeck_config)
            LOGGER.info(
                "Found expired block exam subdeck",
                ankihub_deck_id=subdeck_config.ankihub_deck_id,
                subdeck_id=subdeck_config.subdeck_id,
                due_date=subdeck_config.due_date,
            )

    return expired_subdecks


def move_subdeck_to_main_deck(subdeck_config: BlockExamSubdeckConfig) -> None:
    """Move all notes from a subdeck back to the main deck and delete the subdeck.

    Args:
        subdeck_config: Configuration of the subdeck to move
    """
    ankihub_deck_id = subdeck_config.ankihub_deck_id
    deck_config = config.deck_config(ankihub_deck_id)
    if not deck_config:
        LOGGER.error("Deck config not found for moving subdeck", ankihub_deck_id=str(ankihub_deck_id))
        raise ValueError("Deck config not found")

    subdeck_id = subdeck_config.subdeck_id
    subdeck = aqt.mw.col.decks.get(subdeck_id, default=False)
    if not subdeck:
        LOGGER.warning("Subdeck not found, removing config", subdeck_id=subdeck_config.subdeck_id)
        remove_block_exam_subdeck_config(subdeck_config)
        return

    parent_deck_id = deck_config.anki_id

    note_ids = note_ids_in_deck_hierarchy(subdeck_id)
    if note_ids:
        move_notes_to_decks_while_respecting_odid({nid: parent_deck_id for nid in note_ids})
        LOGGER.info("Moved notes from subdeck to main deck", subdeck_name=subdeck["name"], note_count=len(note_ids))

    aqt.mw.col.decks.remove([subdeck_id])

    remove_block_exam_subdeck_config(subdeck_config)

    LOGGER.info("Successfully moved subdeck to main deck", subdeck_name=subdeck["name"])


def set_subdeck_due_date(subdeck_config: BlockExamSubdeckConfig, new_due_date: Optional[str]) -> None:
    """Set a new due date for a block exam subdeck.

    Args:
        subdeck_config: Current subdeck configuration
        new_due_date: New due date in YYYY-MM-DD format
    """
    updated_config = BlockExamSubdeckConfig(
        ankihub_deck_id=subdeck_config.ankihub_deck_id, subdeck_id=subdeck_config.subdeck_id, due_date=new_due_date
    )

    config.upsert_block_exam_subdeck(updated_config)

    LOGGER.info(
        "Updated subdeck due date",
        ankihub_deck_id=subdeck_config.ankihub_deck_id,
        subdeck_id=subdeck_config.subdeck_id,
        old_due_date=subdeck_config.due_date,
        new_due_date=new_due_date,
    )


def remove_block_exam_subdeck_config(subdeck_config: BlockExamSubdeckConfig) -> None:
    """Remove a block exam subdeck configuration.

    Args:
        subdeck_config: Configuration to remove
    """
    config.remove_block_exam_subdeck(subdeck_config.ankihub_deck_id, subdeck_config.subdeck_id)


def get_subdeck_name_without_parent(subdeck_id: DeckId) -> str:
    """Get the subdeck name without the parent deck prefix.
    E.g. for "MainDeck::Subdeck", returns "Subdeck".
    """
    return aqt.mw.col.decks.name(subdeck_id).split("::", maxsplit=1)[-1]
