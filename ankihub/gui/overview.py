"""Modifies the Anki deck overview screen (aqt.overview)."""

import json
from concurrent.futures import Future
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID

import aqt
from aqt.gui_hooks import overview_did_refresh, webview_did_receive_js_message
from aqt.utils import tooltip
from aqt.webview import AnkiWebView
from jinja2 import Template

from .. import LOGGER
from ..gui.flashcard_selector_dialog import (
    FlashCardSelectorDialog,
    show_flashcard_selector,
)
from ..settings import config
from ..user_state import add_user_state_refreshed_callback
from .deck_updater import ah_deck_updater
from .js_message_handling import parse_js_message_kwargs
from .utils import get_ah_did_of_deck_or_ancestor_deck, robust_filter

OVERVIEW_JS_PATH = Path(__file__).parent / "web/overview.js"
FLASHCARD_SELECTOR_OPEN_BUTTON_ID = "ankihub-flashcard-selector-open-button"
FLASHCARD_SELECTOR_OPEN_PYCMD = "ankihub_flashcard_selector_open"
FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD = "ankihub_sync_notes_actions"
TOUR_PROMPT_TEXT_ID = "ankihub_tour_prompt"
TOUR_OPEN_PYCMD = "ankihub_tour_open"

# Event name dispatched when the suspension state filter should be refreshed
REFRESH_SUSPENSION_FILTER_EVENT_NAME = "refresh-suspension-state-filter"


def setup() -> None:
    """
    Set up overview modifications:
      - Add a button to the deck browser that opens the flashcard selector dialog.
      - Add a link that opens the Step deck tour if the Step deck is selected.
    """

    overview_did_refresh.append(lambda *args, **kwargs: _add_modifications())
    # We need to call this here, because the deck browser is already rendered at this point
    _add_modifications()

    # The button is only added when the feature flag is enabled. The feature flag is fetched in the background,
    # so we might need to add the button when the user state is refreshed.
    add_user_state_refreshed_callback(_add_modifications)

    webview_did_receive_js_message.append(_handle_py_commands)


def _add_modifications() -> None:
    if not aqt.mw.state == "overview":
        return

    ah_did = get_ah_did_of_deck_or_ancestor_deck(aqt.mw.col.decks.current()["id"])

    flashcard_selector_enabled = _should_add_flashcard_selector_button(ah_did)
    tour_enabled = _should_add_step_tour_prompt(ah_did)
    if not (flashcard_selector_enabled or tour_enabled):
        return

    kwargs_json = json.dumps({"deck_id": str(ah_did)}).replace('"', '\\"')
    js = Template(OVERVIEW_JS_PATH.read_text()).render(
        {
            "FLASHCARD_SELECTOR_ENABLED": flashcard_selector_enabled,
            "FLASHCARD_SELECTOR_OPEN_BUTTON_ID": FLASHCARD_SELECTOR_OPEN_BUTTON_ID,
            "FLASHCARD_SELECTOR_OPEN_PYCMD": f"{FLASHCARD_SELECTOR_OPEN_PYCMD} {kwargs_json}",
            "TOUR_ENABLED": tour_enabled,
            "TOUR_PROMPT_TEXT_ID": TOUR_PROMPT_TEXT_ID,
            "TOUR_OPEN_PYCMD": TOUR_OPEN_PYCMD,
        }
    )
    aqt.mw.overview.web.eval(js)


def _should_add_flashcard_selector_button(ah_did: UUID) -> bool:
    if (
        not config.public_config.get("ankihub_smart_search")
        or not config.deck_config(ah_did)
        or not config.deck_config(ah_did).has_note_embeddings
    ):
        return False

    feature_flags = config.get_feature_flags()
    if not feature_flags.get("show_flashcards_selector_button", False):
        LOGGER.debug("Feature flag to show flashcard selector button is disabled, not adding the button.")
        return False

    return True


def _should_add_step_tour_prompt(ah_did: UUID) -> bool:
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("step_deck_tour", False) or ah_did != config.anking_deck_id:
        return False

    return True


@robust_filter
def _handle_py_commands(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
    if message.startswith(FLASHCARD_SELECTOR_OPEN_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        ah_did = UUID(kwargs.get("deck_id"))

        show_flashcard_selector(ah_did=ah_did)

        return (True, None)
    elif message.startswith(FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        deck_id = UUID(kwargs.get("deckId"))

        aqt.mw.taskman.run_in_background(
            lambda: ah_deck_updater.fetch_and_apply_pending_notes_actions_for_deck(deck_id),
            on_done=partial(_on_fetch_and_apply_pending_notes_actions_done, web=context.web),
        )
        return (True, None)
    elif message == TOUR_OPEN_PYCMD:
        from .tutorial import prompt_for_step_deck_tutorial

        prompt_for_step_deck_tutorial()

        return (True, None)
    else:
        return handled


def _on_fetch_and_apply_pending_notes_actions_done(future: Future, web: AnkiWebView) -> None:
    future.result()

    LOGGER.info("Successfully fetched and applied pending notes actions.")
    tooltip(
        "Unsuspended flashcards.",
        parent=(FlashCardSelectorDialog.dialog if FlashCardSelectorDialog.dialog else aqt.mw),
    )

    web.eval(f"window.dispatchEvent(new Event('{REFRESH_SUSPENSION_FILTER_EVENT_NAME}'))")
