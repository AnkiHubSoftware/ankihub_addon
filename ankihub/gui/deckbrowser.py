"""Modifies the Anki deck browser (aqt.deckbrowser)."""

from dataclasses import dataclass
from typing import Optional

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt import QMenu, gui_hooks, qconnect
from aqt.qt import QDialog

from ..main.block_exam_subdecks import move_subdeck_to_main_deck
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import BlockExamSubdeckConfig, config
from .subdeck_due_date_dialog import DatePickerDialog
from .utils import ask_user, get_ah_did_of_deck_or_ancestor_deck


@dataclass
class _DatePickerDialogState:
    """State for keeping date picker dialog reference alive."""

    dialog: Optional[QDialog] = None


_dialog_state = _DatePickerDialogState()


def setup() -> None:
    """Ask the user if they want to unsubscribe from the AnkiHub deck when they delete the associated Anki deck."""

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
        new=_after_anki_deck_deleted,
    )
    setup_subdeck_ankihub_options()


def _open_dialog_date_picker_for_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    subdeck_name = aqt.mw.col.decks.get(subdeck_config.subdeck_id)["name"].split("::", maxsplit=1)[-1]

    _dialog_state.dialog = DatePickerDialog(subdeck_name=subdeck_name, subdeck_config=subdeck_config, parent=aqt.mw)
    _dialog_state.dialog.open()


def _remove_block_exam_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    move_subdeck_to_main_deck(subdeck_config)
    aqt.mw.deckBrowser.refresh()


def _setup_update_subdeck_due_date(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Ankihub: Update due date")

    subdeck_config = config.get_block_exam_subdeck_config(subdeck_did)

    action.setEnabled(subdeck_config is not None)

    if subdeck_config:
        action.setToolTip("Change the due date of this subdeck.")
        qconnect(action.triggered, lambda: _open_dialog_date_picker_for_subdeck(subdeck_config))
    else:
        action.setToolTip("This option is only available for subdecks created with SmartSearch")


def _setup_remove_block_exam_subdeck(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Ankihub: Remove subdeck")

    subdeck_config = config.get_block_exam_subdeck_config(subdeck_did)

    action.setEnabled(subdeck_config is not None)

    if subdeck_config:
        action.setToolTip("Deletes the subdeck and moves all notes back into the main deck.")
        qconnect(action.triggered, lambda: _remove_block_exam_subdeck(subdeck_config))
    else:
        action.setToolTip("This option is only available for subdecks created with SmartSearch")


def _initialize_subdeck_context_menu_actions(menu: QMenu, deck_id: int) -> None:
    # Only show the menu actions for descendants of AnkiHub decks
    is_descendant_of_ah_deck = (
        get_ah_did_of_deck_or_ancestor_deck(DeckId(deck_id)) is not None
        and aqt.mw.col.decks.parents(DeckId(deck_id))  # Ensure it's not a top-level deck
    )
    if not is_descendant_of_ah_deck:
        return

    menu.setToolTipsVisible(True)
    _setup_update_subdeck_due_date(menu, DeckId(deck_id))
    _setup_remove_block_exam_subdeck(menu, DeckId(deck_id))


def setup_subdeck_ankihub_options() -> None:
    gui_hooks.deck_browser_will_show_options_menu.append(_initialize_subdeck_context_menu_actions)
