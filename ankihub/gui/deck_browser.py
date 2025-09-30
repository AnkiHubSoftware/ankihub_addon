"""Modifies the Anki deck browser (aqt.deck_browser)."""

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt import QMenu, gui_hooks, qconnect

from .. import LOGGER
from ..gui.subdeck_due_date_dialog import DatePickerDialog
from ..main.block_exam_subdecks import move_subdeck_to_main_deck
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import (
    BlockExamSubdeckConfig,
    config,
)
from .utils import ask_user


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


def _open_dialog_date_picker_for_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    if not subdeck_config:
        LOGGER.warning("Subdeck with ID %s not found in configuration.", subdeck_config.subdeck_id)
        return

    subdeck_name = aqt.mw.col.decks.get(subdeck_config.subdeck_id)["name"].split("::", maxsplit=1)[-1]

    DatePickerDialog(subdeck_name=subdeck_name, subdeck_config=subdeck_config, parent=aqt.mw).exec()


def _remove_block_exam_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    if not subdeck_config:
        LOGGER.warning("Subdeck with ID %s not found in configuration.", subdeck_config.subdeck_id)
        return

    move_subdeck_to_main_deck(subdeck_config)
    aqt.mw.deckBrowser.refresh()


def _setup_update_subdeck_due_date(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Ankihub: Update Subdeck due date")
    action.setToolTip("This option is only available for subdecks created with SmartSearch")

    subdecks = config.get_block_exam_subdecks()
    subdeck_exists = False
    if subdecks:
        subdeck_exists = any(int(sd.subdeck_id) == int(subdeck_did) for sd in subdecks)

    action.setEnabled(subdeck_exists)

    if subdeck_exists:
        action.setToolTip("Change the due date of this subdeck.")
        subdeck_config = next((sd for sd in subdecks if int(sd.subdeck_id) == int(subdeck_did)), None)
        qconnect(action.triggered, lambda: _open_dialog_date_picker_for_subdeck(subdeck_config))


def _setup_remove_block_exam_subdeck(menu: QMenu, subdeck_did: DeckId) -> None:
    action = menu.addAction("Ankihub: Remove subdeck")
    action.setToolTip("This option is only available for subdecks created with SmartSearch")

    subdecks = config.get_block_exam_subdecks()
    subdeck_exists = False
    if subdecks:
        subdeck_exists = any(int(sd.subdeck_id) == int(subdeck_did) for sd in subdecks)

    action.setEnabled(subdeck_exists)

    if subdeck_exists:
        action.setToolTip("Deletes the subdeck and moves all notes back into the main deck.")
        subdeck_config = next((sd for sd in subdecks if int(sd.subdeck_id) == int(subdeck_did)), None)
        qconnect(action.triggered, lambda: _remove_block_exam_subdeck(subdeck_config))


def _on_subdeck_ankihub_options_show(menu: QMenu, subdeck_did: DeckId) -> None:
    menu.setToolTipsVisible(True)
    _setup_update_subdeck_due_date(menu, subdeck_did)
    _setup_remove_block_exam_subdeck(menu, subdeck_did)


def setup_subdeck_ankihub_options() -> None:
    gui_hooks.deck_browser_will_show_options_menu.append(_on_subdeck_ankihub_options_show)
