"""Modifies the Anki deck overview screen (aqt.overview)."""

from concurrent.futures import Future
from functools import partial
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import aqt
from anki.decks import DeckId
from aqt.gui_hooks import overview_did_refresh, webview_did_receive_js_message
from aqt.utils import tooltip
from aqt.webview import AnkiWebView
from jinja2 import Template

from .. import LOGGER
from ..feature_flags import add_feature_flags_update_callback, feature_flags
from ..settings import config, url_flashcard_selector, url_flashcard_selector_embed
from .deck_updater import ah_deck_updater
from .js_message_handling import parse_js_message_kwargs
from .menu import AnkiHubLogin
from .webview import AnkiHubWebViewDialog

ADD_FLASHCARD_SELECTOR_BUTTON_JS_PATH = (
    Path(__file__).parent / "web/add_flashcard_selector_button.js"
)
FLASHCARD_SELECTOR_OPEN_BUTTON_ID = "ankihub-flashcard-selector-open-button"
FLASHCARD_SELECTOR_OPEN_PYCMD = "ankihub_flashcard_selector_open"

FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD = "ankihub_sync_notes_actions"

# Event name dispatched when the suspension state filter should be refreshed
REFRESH_SUSPENSION_FILTER_EVENT_NAME = "refresh-suspension-state-filter"


def setup() -> None:
    """Add a button to the deck browser that opens the flashcard selector dialog."""
    overview_did_refresh.append(
        lambda *args, **kwargs: _maybe_add_flashcard_selector_button()
    )
    # We need to call this here, because the deck browser is already rendered at this point
    _maybe_add_flashcard_selector_button()

    # The button is only added when the feature flag is enabled. The feature flag is fetched in the background,
    # so we might need to add the button when the feature flag is fetched.
    add_feature_flags_update_callback(_maybe_add_flashcard_selector_button)

    webview_did_receive_js_message.append(_handle_flashcard_selector_py_commands)


def _maybe_add_flashcard_selector_button() -> None:
    """Add the flashcard selector button to the Anking deck overview."""

    if not aqt.mw.state == "overview":
        return

    if not _deck_or_ancestor_has_note_embeddings(aqt.mw.col.decks.current()["id"]):
        return

    if not feature_flags.show_flashcards_selector_button:
        LOGGER.debug(
            "Feature flag to show flashcard selector button is disabled, not adding the button."
        )
        return

    overview_web: AnkiWebView = aqt.mw.overview.web
    js = Template(ADD_FLASHCARD_SELECTOR_BUTTON_JS_PATH.read_text()).render(
        {
            "FLASHCARD_SELECTOR_OPEN_BUTTON_ID": FLASHCARD_SELECTOR_OPEN_BUTTON_ID,
            "FLASHCARD_SELECTOR_OPEN_PYCMD": FLASHCARD_SELECTOR_OPEN_PYCMD,
        }
    )
    overview_web.eval(js)


def _deck_or_ancestor_has_note_embeddings(anki_did: DeckId) -> bool:
    anki_dids = [anki_did] + [deck["id"] for deck in aqt.mw.col.decks.parents(anki_did)]
    return any(
        (deck_config := config.deck_config_by_anki_did(anki_did))
        and deck_config.has_note_embeddings
        for anki_did in anki_dids
    )


def _handle_flashcard_selector_py_commands(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if message == FLASHCARD_SELECTOR_OPEN_PYCMD:
        FlashCardSelectorDialog.display(aqt.mw)
        LOGGER.info("Opened flashcard selector dialog.")
        return (True, None)
    elif message.startswith(FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        deck_id = UUID(kwargs.get("deckId"))

        aqt.mw.taskman.run_in_background(
            lambda: ah_deck_updater.fetch_and_apply_pending_notes_actions_for_deck(
                deck_id
            ),
            on_done=partial(
                _on_fetch_and_apply_pending_notes_actions_done, web=context.web
            ),
        )
        return (True, None)
    else:
        return handled


def _on_fetch_and_apply_pending_notes_actions_done(
    future: Future, web: AnkiWebView
) -> None:
    future.result()

    LOGGER.info("Successfully fetched and applied pending notes actions.")
    tooltip(
        "Unsuspended flashcards.",
        parent=(
            FlashCardSelectorDialog.dialog if FlashCardSelectorDialog.dialog else aqt.mw
        ),
    )

    web.eval(
        f"window.dispatchEvent(new Event('{REFRESH_SUSPENSION_FILTER_EVENT_NAME}'))"
    )


class FlashCardSelectorDialog(AnkiHubWebViewDialog):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)

    def _setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub | Flashcard Selector")
        self.resize(1000, 800)

        super()._setup_ui()

    def _get_embed_url(self) -> str:
        return url_flashcard_selector_embed(config.anking_deck_id)

    def _get_non_embed_url(self) -> str:
        return url_flashcard_selector(config.anking_deck_id)

    @classmethod
    def _handle_auth_failure(cls) -> None:
        # Close the flashcard selector dialog and prompt them to log in,
        # then they can open the dialog again
        if cls.dialog:
            cls.dialog = cast(FlashCardSelectorDialog, cls.dialog)
            cls.dialog.close()

        AnkiHubLogin.display_login()
        LOGGER.info(
            "Prompted user to log in to AnkiHub, after failed authentication in flashcard selector."
        )
