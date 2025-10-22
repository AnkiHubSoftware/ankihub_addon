"""Modifies the Anki deck browser (aqt.deckbrowser)."""

from dataclasses import dataclass
from typing import Optional

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt import QMenu, gui_hooks, qconnect
from aqt.qt import QDialog, QDialogButtonBox, QFont

from .. import LOGGER
from ..main.block_exam_subdecks import (
    get_subdeck_log_context,
    get_subdeck_name_without_parent,
    move_subdeck_to_main_deck,
)
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import ActionSource, BlockExamSubdeckOrigin, config
from .operations.user_details import check_user_feature_access
from .subdeck_due_date_dialog import SubdeckDueDatePickerDialog
from .utils import ask_user, show_dialog, show_tooltip


@dataclass
class _DatePickerDialogState:
    """State for keeping date picker dialog reference alive."""

    dialog: Optional[QDialog] = None


_dialog_state = _DatePickerDialogState()


def setup() -> None:
    """Ask the user if they want to unsubscribe from the AnkiHub deck when they delete the associated Anki deck."""

    def _before_anki_deck_deleted(did: DeckId) -> None:
        """Log subdeck deletion before the deck is deleted."""
        # Check if this is a subdeck (has parents) and not a filtered deck
        if not aqt.mw.col.decks.parents(did) or aqt.mw.col.decks.is_filtered(did):
            return

        subdeck_config = config.get_block_exam_subdeck_config(did)
        if not subdeck_config:
            return

        LOGGER.info(
            "block_exam_subdeck_deleted",
            **get_subdeck_log_context(did, ActionSource.DECK_CONTEXT_MENU),
        )

    def _after_anki_deck_deleted(did: DeckId) -> None:
        deck_ankihub_id = config.get_deck_uuid_by_did(did)
        if not deck_ankihub_id:
            return
        deck_name = config.deck_config(deck_ankihub_id).name
        if ask_user(
            text="You've deleted the Anki deck linked to the<br>"
            f"<b>{deck_name}</b> AnkiHub deck.<br><br>"
            "Do you also want to unsubscribe from this AnkiHub deck to avoid receiving future updates?<br><br>"
            "For more info, check out "
            "<a href='https://community.ankihub.net/t/how-are-anki-decks-related-to-ankihub-decks/4811/1'>"
            "this topic on our forum</a>.",
            title="Unsubscribe from AnkiHub Deck?",
            parent=aqt.mw,
        ):
            unsubscribe_from_deck_and_uninstall(deck_ankihub_id)

    aqt.mw.deckBrowser._delete = wrap(  # type: ignore
        old=aqt.mw.deckBrowser._delete,
        new=_before_anki_deck_deleted,
        pos="before",
    )
    aqt.mw.deckBrowser._delete = wrap(  # type: ignore
        old=aqt.mw.deckBrowser._delete,
        new=_after_anki_deck_deleted,
    )
    setup_subdeck_ankihub_options()


def _open_date_picker_dialog_for_subdeck(subdeck_id: DeckId, initial_due_date: Optional[str]) -> None:
    _dialog_state.dialog = SubdeckDueDatePickerDialog(
        subdeck_id=subdeck_id,
        origin_hint=BlockExamSubdeckOrigin.DECK_CONTEXT_MENU,
        initial_due_date=initial_due_date,
        parent=aqt.mw,
        action_source=ActionSource.DECK_CONTEXT_MENU,
    )
    _dialog_state.dialog.show()


def _open_remove_block_exam_subdeck_dialog(subdeck_id: DeckId) -> None:
    subdeck_name = get_subdeck_name_without_parent(subdeck_id)

    def on_button_clicked(button_index: int) -> None:
        if button_index != 1:
            return

        note_count = move_subdeck_to_main_deck(subdeck_id, action_source=ActionSource.DECK_CONTEXT_MENU)
        aqt.mw.deckBrowser.refresh()

        show_tooltip(f"{note_count} notes merged into the main deck", parent=aqt.mw)

    show_dialog(
        text=(
            "<span style='font-size: 16px; font-weight: bold;'>"
            "Are you sure you want to merge this subdeck?"
            "</span>"
            "<br><br>"
            f"Merging the subdeck <b>{subdeck_name}</b> will move all its notes, "
            "including those from nested subdecks, back to the main deck."
        ),
        title="AnkiHub | Subdecks",
        buttons=[
            ("Cancel", QDialogButtonBox.ButtonRole.RejectRole),
            ("Merge subdeck", QDialogButtonBox.ButtonRole.AcceptRole),
        ],
        default_button_idx=1,
        callback=on_button_clicked,
        use_show=True,
        modal=True,
        add_title_to_body_on_mac=False,
        parent=aqt.mw,
    )


def _setup_update_subdeck_due_date(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Set due date")

    initial_due_date = config.get_block_exam_subdeck_due_date(subdeck_did)

    action.setToolTip("Set the due date of this subdeck.")
    qconnect(
        action.triggered,
        lambda: _open_date_picker_dialog_for_subdeck(subdeck_did, initial_due_date),
    )


def _setup_remove_block_exam_subdeck(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Merge into parent deck")

    action.setToolTip("Deletes the subdeck and moves all notes back into the main deck.")
    qconnect(action.triggered, lambda: _open_remove_block_exam_subdeck_dialog(subdeck_did))


def _initialize_subdeck_context_menu_actions(menu: QMenu, deck_id: int) -> None:
    did = DeckId(deck_id)

    # Only show the menu actions for subdecks which are not filtered decks
    if not aqt.mw.col.decks.parents(did) or aqt.mw.col.decks.is_filtered(did):
        return

    if not config.get_feature_flags().get("block_exam_subdecks"):
        return

    def on_access_granted(_: dict) -> None:
        menu.setToolTipsVisible(True)

        menu.addSeparator()

        # Add AnkiHub section header
        label_action = menu.addAction("ANKIHUB")
        label_action.setEnabled(False)
        font = QFont()
        font.setBold(True)
        font.setPointSize(10)
        label_action.setFont(font)

        _setup_update_subdeck_due_date(menu, did)
        _setup_remove_block_exam_subdeck(menu, did)

    check_user_feature_access(
        feature_key="has_flashcard_selector_access",
        on_access_granted=on_access_granted,
    )


def setup_subdeck_ankihub_options() -> None:
    gui_hooks.deck_browser_will_show_options_menu.append(_initialize_subdeck_context_menu_actions)
