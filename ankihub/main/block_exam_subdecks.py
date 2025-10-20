"""Functions for managing block exam subdecks."""

from datetime import date, datetime
from typing import List, Optional, Tuple

import aqt
from anki.decks import DeckId
from anki.notes import NoteId
from anki.utils import ids2str
from aqt.operations.scheduling import unsuspend_cards

from .. import LOGGER
from ..settings import BlockExamSubdeckConfig, BlockExamSubdeckConfigOrigin, config
from .utils import move_notes_to_decks_while_respecting_odid, note_ids_in_deck_hierarchy


def get_root_deck_id_from_subdeck(subdeck_id: DeckId) -> DeckId:
    """Get the root (top-level) deck ID for a given subdeck.

    Args:
        subdeck_id: The ID of the subdeck

    Returns:
        The ID of the root deck (top-level ancestor), or the subdeck_id itself if it has no parents
    """
    parents = aqt.mw.col.decks.parents(subdeck_id)
    return parents[0]["id"] if parents else subdeck_id


def create_block_exam_subdeck(
    root_deck_id: DeckId,
    subdeck_name: str,
    due_date: Optional[str],
    origin_hint: BlockExamSubdeckConfigOrigin,
    action_source: Optional[str] = None,
) -> Tuple[str, bool]:
    """Create a new block exam subdeck and its configuration.

    Args:
        root_deck_id: The ID of the root deck
        subdeck_name: Name of the subdeck (without parent deck prefix)
        due_date: Due date for the subdeck in YYYY-MM-DD format, or None
        origin_hint: Origin of this subdeck creation

    Args:
        root_deck_id: The ID of the root deck
        subdeck_name: Name of the subdeck (without parent deck prefix)
        due_date: Due date for the subdeck in YYYY-MM-DD format

    Returns:
        Tuple of (actual_subdeck_name, was_renamed)
        was_renamed is True if the name was modified to avoid conflicts
    """
    root_deck = aqt.mw.col.decks.get(root_deck_id, default=False)
    if not root_deck:
        raise ValueError("Root deck not found")

    root_deck_name = root_deck["name"]

    # Check for conflicts and generate unique name if needed
    full_subdeck_name = f"{root_deck_name}::{subdeck_name}"
    original_name = subdeck_name
    counter = 1

    while aqt.mw.col.decks.by_name(full_subdeck_name) is not None:
        subdeck_name = f"{original_name} ({counter})"
        full_subdeck_name = f"{root_deck_name}::{subdeck_name}"
        counter += 1

    # Create the subdeck
    subdeck_id = DeckId(aqt.mw.col.decks.add_normal_deck_with_name(full_subdeck_name).id)

    # Make the subdeck inherit the root deck's option group
    subdeck = aqt.mw.col.decks.get(subdeck_id)
    subdeck["conf"] = root_deck["conf"]
    aqt.mw.col.decks.update(subdeck)

    # Save configuration
    subdeck_config = config.upsert_block_exam_subdeck(
        subdeck_id,
        due_date=due_date,
        origin_hint=origin_hint,
    )

    was_renamed = subdeck_name != original_name

    LOGGER.info(
        "block_exam_subdeck_created",
        action_source=action_source,
        ankihub_deck_id=config.get_deck_uuid_by_did(root_deck_id),
        subdeck_name=subdeck_name,
        subdeck_full_name=subdeck["name"],
        subdeck_config=subdeck_config.to_dict(),
        due_date=due_date,
        was_renamed=was_renamed,
    )

    return subdeck_name, was_renamed


def add_notes_to_block_exam_subdeck(
    root_deck_id: DeckId,
    subdeck_name: str,
    note_ids: List[NoteId],
    due_date: Optional[str],
    origin_hint: BlockExamSubdeckConfigOrigin,
    unsuspend_notes: bool = False,
    action_source: Optional[str] = None,
) -> int:
    """Add notes to a block exam subdeck and create/update its configuration.

    A configuration entry is created if it doesn't exist, or updated if it does.

    Args:
        root_deck_id: The ID of the root deck
        subdeck_name: Name of the subdeck (without parent deck prefix)
        note_ids: List of note IDs to add to the subdeck
        due_date: Due date for the subdeck in YYYY-MM-DD format, or None
        origin_hint: Origin of this subdeck operation
        unsuspend_notes: Whether to unsuspend the notes after adding them

    Returns:
        The number of notes actually moved to the subdeck (excluding notes already in the subdeck).
    """
    if not note_ids:
        return 0

    root_deck = aqt.mw.col.decks.get(root_deck_id, default=False)
    if not root_deck:
        raise ValueError("Root deck not found")

    root_deck_name = root_deck["name"]
    full_subdeck_name = f"{root_deck_name}::{subdeck_name}"
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

    # Update configuration
    subdeck_config = config.upsert_block_exam_subdeck(
        subdeck["id"],
        due_date=due_date,
        origin_hint=origin_hint,
    )

    ah_did = config.get_deck_uuid_by_did(get_root_deck_id_from_subdeck(subdeck_id))
    LOGGER.info(
        "block_exam_subdeck_notes_added",
        action_source=action_source,
        ankihub_deck_id=ah_did,
        subdeck_name=subdeck_name,
        subdeck_full_name=subdeck["name"],
        subdeck_config=subdeck_config.to_dict(),
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
                subdeck_id=subdeck_config.subdeck_id,
                due_date=subdeck_config.due_date,
            )

    return expired_subdecks


def move_subdeck_to_main_deck(subdeck_id: DeckId, action_source: Optional[str] = None) -> int:
    """Move all notes from a subdeck back to the root deck, delete the subdeck,
    and remove its configuration (if it exists).

    Args:
        subdeck_id: The Anki subdeck ID

    Returns:
        The number of notes moved to the root deck

    Raises:
        ValueError: If the provided deck is a root deck (not a subdeck)
    """
    subdeck_config = config.get_block_exam_subdeck_config(subdeck_id)

    subdeck = aqt.mw.col.decks.get(subdeck_id, default=False)
    if not subdeck:
        LOGGER.warning("Subdeck not found, removing config if exists", subdeck_id=subdeck_id)
        if subdeck_config:
            config.remove_block_exam_subdeck(subdeck_id)
        return 0

    root_deck_id = get_root_deck_id_from_subdeck(subdeck_id)
    if root_deck_id == subdeck_id:
        raise ValueError("The provided deck isn't a subdeck.")

    note_ids = note_ids_in_deck_hierarchy(subdeck_id)
    note_count = len(note_ids) if note_ids else 0

    if note_ids:
        move_notes_to_decks_while_respecting_odid({nid: root_deck_id for nid in note_ids})
        LOGGER.info("Moved notes from subdeck to root deck", subdeck_name=subdeck["name"], note_count=note_count)

    aqt.mw.col.decks.remove([subdeck_id])

    if subdeck_config:
        config.remove_block_exam_subdeck(subdeck_id)

    ah_did = config.get_deck_uuid_by_did(get_root_deck_id_from_subdeck(subdeck_id))
    LOGGER.info(
        "subdeck_merged_into_main_deck",
        action_source=action_source,
        ankihub_deck_id=ah_did,
        subdeck_name=get_subdeck_name_without_parent(subdeck_id),
        subdeck_full_name=subdeck["name"],
        subdeck_config=subdeck_config.to_dict() if subdeck_config else None,
        due_date=subdeck_config.due_date if subdeck_config else None,
    )

    return note_count


def set_subdeck_due_date(
    subdeck_id: DeckId,
    new_due_date: Optional[str],
    origin_hint: BlockExamSubdeckConfigOrigin,
    action_source: Optional[str] = None,
) -> None:
    """Set or clear the due date for a block exam subdeck.

    A configuration entry is created if it doesn't exist.

    Args:
        subdeck_id: The Anki subdeck ID
        new_due_date: New due date in YYYY-MM-DD format, or None to clear the due date
        origin_hint: Origin of this subdeck operation (used only if creating a new config)

    Raises:
        ValueError: If subdeck not found
    """
    # Validate subdeck exists
    subdeck = aqt.mw.col.decks.get(subdeck_id, default=False)
    if not subdeck:
        raise ValueError(f"Subdeck with ID {subdeck_id} not found")

    existing_config = config.get_block_exam_subdeck_config(subdeck_id)
    old_due_date = existing_config.due_date if existing_config else None

    subdeck_config = config.upsert_block_exam_subdeck(
        subdeck_id,
        due_date=new_due_date,
        origin_hint=origin_hint,
    )

    ah_did = config.get_deck_uuid_by_did(get_root_deck_id_from_subdeck(subdeck_id))
    LOGGER.info(
        "subdeck_due_date_changed",
        action_source=action_source,
        ankihub_deck_id=ah_did,
        subdeck_name=get_subdeck_name_without_parent(subdeck_id),
        subdeck_full_name=subdeck["name"],
        subdeck_config=subdeck_config.to_dict(),
        old_due_date=old_due_date,
        due_date=new_due_date,
    )


def get_exam_subdecks(root_deck_id: DeckId) -> list[tuple[str, DeckId]]:
    """Get descendants of the given root deck which are block exam subdeck.

    Returns a list of (name, id) tuples.
    Doesn't return exam subdecks that don't exist in Anki.
    """
    # Get all child decks under root
    child_decks = aqt.mw.col.decks.children(root_deck_id)

    # Get exam subdeck IDs from config
    exam_subdeck_configs = config.get_block_exam_subdecks()
    exam_subdeck_ids = {int(cfg.subdeck_id) for cfg in exam_subdeck_configs}

    # Filter children to only include exam subdecks
    return [(name, deck_id) for name, deck_id in child_decks if deck_id in exam_subdeck_ids]


def get_subdeck_name_without_parent(subdeck_id: DeckId) -> str:
    """Get the subdeck name without the parent deck prefix.
    E.g. for "MainDeck::Subdeck", returns "Subdeck".
    """
    return aqt.mw.col.decks.name(subdeck_id).split("::", maxsplit=1)[-1]
