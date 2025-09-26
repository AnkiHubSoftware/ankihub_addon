"""Functions for managing block exam subdecks."""

import uuid
from datetime import date, datetime
from typing import List, Optional, Tuple

import aqt
from anki.notes import NoteId

from .. import LOGGER
from ..settings import BlockExamSubdeckConfig, config
from .utils import move_notes_to_decks_while_respecting_odid


def get_existing_block_exam_subdecks(ankihub_deck_id: uuid.UUID) -> List[Tuple[str, str]]:
    """Get list of existing block exam subdecks for a deck.

    Returns:
        List of tuples (subdeck_name, subdeck_id)
    """
    deck_config = config.deck_config(ankihub_deck_id)
    if not deck_config:
        return []

    anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
    if not anki_deck_name:
        return []

    # Find all subdecks that contain block exam notes
    subdecks = []
    for child_name, child_id in aqt.mw.col.decks.children(deck_config.anki_id):
        # Check if this subdeck has block exam configuration
        if config.get_block_exam_subdeck_due_date(str(ankihub_deck_id), str(child_id)):
            subdeck_name = child_name[len(anki_deck_name) + 2 :]  # +2 for the '::' separator
            subdecks.append((subdeck_name, str(child_id)))

    return subdecks


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
    subdeck_id = aqt.mw.col.decks.add_normal_deck_with_name(full_subdeck_name).id

    # Save configuration if due date provided
    if due_date:
        config_item = BlockExamSubdeckConfig(
            ankihub_deck_id=str(ankihub_deck_id), subdeck_id=str(subdeck_id), due_date=due_date
        )
        config.add_block_exam_subdeck(config_item)

    LOGGER.info("Created block exam subdeck", subdeck_name=subdeck_name, due_date=due_date)

    return subdeck_name, subdeck_name != original_name


def add_notes_to_block_exam_subdeck(
    ankihub_deck_id: uuid.UUID, subdeck_name: str, note_ids: List[NoteId], due_date: Optional[str] = None
) -> None:
    """Add notes to a block exam subdeck and update configuration."""
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

    # Move notes to subdeck
    move_notes_to_decks_while_respecting_odid({nid: subdeck["id"] for nid in note_ids})

    # Update configuration with due date
    if due_date:
        config_item = BlockExamSubdeckConfig(
            ankihub_deck_id=str(ankihub_deck_id), subdeck_id=str(subdeck["id"]), due_date=due_date
        )
        config.add_block_exam_subdeck(config_item)

    LOGGER.info(
        "Added notes to block exam subdeck", subdeck_name=subdeck_name, note_count=len(note_ids), due_date=due_date
    )


def check_block_exam_subdeck_due_dates() -> List[BlockExamSubdeckConfig]:
    """Check for block exam subdecks that are past their due date.

    Returns:
        List of expired subdeck configurations
    """
    expired_subdecks = []
    today = date.today()

    for subdeck_config in config.get_block_exam_subdecks():
        try:
            due_date = datetime.strptime(subdeck_config.due_date, "%Y-%m-%d").date()

            if today >= due_date:
                expired_subdecks.append(subdeck_config)
                LOGGER.info(
                    "Found expired block exam subdeck",
                    ankihub_deck_id=subdeck_config.ankihub_deck_id,
                    subdeck_id=subdeck_config.subdeck_id,
                    due_date=subdeck_config.due_date,
                )
        except (ValueError, TypeError) as e:
            LOGGER.warning(
                "Invalid due date format for block exam subdeck",
                ankihub_deck_id=subdeck_config.ankihub_deck_id,
                subdeck_id=subdeck_config.subdeck_id,
                due_date=subdeck_config.due_date,
                error=str(e),
            )

    return expired_subdecks


def trigger_due_date_dialog(expired_subdecks: List[BlockExamSubdeckConfig]) -> None:
    """Trigger the Due Date Dialog for expired subdecks.

    This is a placeholder function that will be implemented when the
    Due Date Dialog spec is ready.

    Args:
        expired_subdecks: List of expired subdeck configurations
    """
    if not expired_subdecks:
        return
    # TODO: Implement actual dialog triggering
    aqt.utils.showInfo(f"{len(expired_subdecks)} block exam subdeck(s) have reached their due date.")


def check_and_handle_block_exam_subdeck_due_dates() -> None:
    """Check for expired block exam subdecks and trigger dialog if any are found."""
    try:
        expired_subdecks = check_block_exam_subdeck_due_dates()
        if expired_subdecks:
            trigger_due_date_dialog(expired_subdecks)
    except Exception as e:
        # Don't let due date checking crash the sync or startup
        LOGGER.error("Error checking block exam subdeck due dates", exc_info=e)
