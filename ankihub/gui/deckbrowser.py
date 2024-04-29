"""Modifies the Anki deck browser (aqt.deckbrowser)."""

from concurrent.futures import Future
from typing import Any, cast
from uuid import UUID

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt.gui_hooks import deck_browser_did_render, webview_did_receive_js_message
from aqt.utils import tooltip
from aqt.webview import AnkiWebView

from .. import LOGGER
from ..feature_flags import add_feature_flags_update_callback, feature_flags
from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import (
    ANKING_DECK_ID,
    config,
    url_flashcard_selector,
    url_flashcard_selector_embed,
)
from .deck_updater import ah_deck_updater
from .menu import AnkiHubLogin
from .utils import ask_user
from .webview import AnkiHubWebViewDialog

FLASHCARD_SELECTOR_OPEN_BUTTON_ID = "ankihub-flashcard-selector-open-button"
FLASHCARD_SELECTOR_OPEN_PYCMD = "ankihub_flashcard_selector_open"

FLASHCARD_SELCTOR_UNSUSPEND_FLASHCARDS_BUTTON_ID_PREFIX = "select-flashcards-button"
FLASHCARD_SELECTOR_FORM_DATA_DIV_ID_PREFIX = "unsuspend-cards-data"
FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD = "ankihub_sync_notes_actions"


def setup() -> None:
    _setup_flashcard_selector_button()
    _setup_deck_delete_hook()


def _setup_flashcard_selector_button() -> None:
    """Add a button to the deck browser that opens the flashcard selector dialog."""
    deck_browser_did_render.append(
        lambda *args, **kwargs: _maybe_add_flashcard_selector_button()
    )
    # We need to call this here, because the deck browser is already rendered at this point
    _maybe_add_flashcard_selector_button()

    # The button is only added when the feature flag is enabled. The feature flag is fetched in the background,
    # so we might need to add the button when the feature flag is fetched.
    add_feature_flags_update_callback(_maybe_add_flashcard_selector_button)

    webview_did_receive_js_message.append(_handle_flashcard_selector_py_commands)


def _maybe_add_flashcard_selector_button() -> None:
    """Add the flashcard selector button to the Anking deck if it exists."""
    if not (deck_config := config.deck_config(ANKING_DECK_ID)):
        return

    if not feature_flags.show_flashcards_selector_button:
        LOGGER.info(
            "Feature flag to show flashcard selector button is disabled, not adding the button."
        )
        return

    deck_browser_web: AnkiWebView = aqt.mw.deckBrowser.web
    deck_browser_web.eval(_js_add_flashcard_selector_button(deck_config.anki_id))


def _js_add_flashcard_selector_button(anki_deck_id: DeckId) -> str:
    return f"""
        if(!document.getElementById("{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}")) {{
            var button = document.createElement("button");
            button.id = "{FLASHCARD_SELECTOR_OPEN_BUTTON_ID}";
            button.innerHTML = "Select flashcards";

            button.addEventListener("click", function() {{
              pycmd("{FLASHCARD_SELECTOR_OPEN_PYCMD}");
            }});

            var deckElement = document.querySelector(".deck[id='{anki_deck_id}']");
            deckElement.appendChild(button);
        }}
    """


def _handle_flashcard_selector_py_commands(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if message == FLASHCARD_SELECTOR_OPEN_PYCMD:
        FlashCardSelectorDialog.display(aqt.mw)
        LOGGER.info("Opened flashcard selector dialog.")
        return (True, None)
    elif message.startswith(FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD):
        _, ah_did_str = message.split(" ")
        aqt.mw.taskman.run_in_background(
            lambda: ah_deck_updater.fetch_and_apply_pending_notes_actions_for_deck(
                UUID(ah_did_str)
            ),
            on_done=_on_fetch_and_apply_pending_notes_actions_done,
        )
        return (True, None)
    else:
        return handled


def _on_fetch_and_apply_pending_notes_actions_done(future: Future) -> None:
    future.result()

    LOGGER.info("Successfully fetched and applied pending notes actions.")
    tooltip(
        "Unsuspended flashcards.",
        parent=(
            FlashCardSelectorDialog.dialog if FlashCardSelectorDialog.dialog else aqt.mw
        ),
    )


class FlashCardSelectorDialog(AnkiHubWebViewDialog):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)

    def _setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub | Flashcard Selector")
        self.setMinimumHeight(800)
        self.setMinimumWidth(900)

        super()._setup_ui()

    def _get_embed_url(self) -> str:
        return url_flashcard_selector_embed(ANKING_DECK_ID)

    def _get_non_embed_url(self) -> str:
        return url_flashcard_selector(ANKING_DECK_ID)

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

    def _on_successful_page_load(self) -> None:
        self.web.eval(
            f"""
            setInterval(function() {{
                // Notify python to sync notes actions after the notes action is created for
                // the selected flashcards.
                const unsuspendButtons = document.querySelectorAll(
                    '[id^="{FLASHCARD_SELCTOR_UNSUSPEND_FLASHCARDS_BUTTON_ID_PREFIX}"]'
                );
                for (const unsuspendButton of unsuspendButtons) {{
                    if (unsuspendButton && !unsuspendButton.appliedModifications) {{
                        unsuspendButton.setAttribute("x-on:htmx:after-request", "ankihubHandleUnsuspendNotesResponse")
                        htmx.process(unsuspendButton);

                        unsuspendButton.appliedModifications = true;
                        console.log("Added htmx:after-request attribute to unsuspend button.");

                    }}
                }}

                // Add a hidden input to the form to disable the success notification. We are using a different
                // notification with the flashcard selector dialog.
                const unsuspendCardsDataDivs = document.querySelectorAll(
                    '[id^="{FLASHCARD_SELECTOR_FORM_DATA_DIV_ID_PREFIX}"]'
                );
                for (const unsuspendCardDataDiv of unsuspendCardsDataDivs) {{
                    if (unsuspendCardDataDiv && !unsuspendCardDataDiv.appliedModifications) {{
                        const showNotificationInput = document.createElement("input");
                        unsuspendCardDataDiv.appendChild(showNotificationInput);
                        showNotificationInput.outerHTML = `
                            <input type="hidden" name="show-success-notification" value="false">
                        `
                        unsuspendCardDataDiv.appliedModifications = true;
                        console.log("Added hidden input to disable success notification.");
                    }}
                }}
            }}, 100);

            window.ankihubHandleUnsuspendNotesResponse = function(event) {{
                if (event.detail.xhr.status === 201) {{
                    // Extract deck id from the url of the page
                    const uuidRegex = /[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}/;
                    const deckId = uuidRegex.exec(window.location.href)[0];

                    // Notify python to sync notes actions for the deck
                    console.log(`Unsuspending notes for deckId=${{deckId}}`);
                    pycmd(`{FLASHCARD_SELECTOR_SYNC_NOTES_ACTIONS_PYCMD} ${{deckId}}`);
                }} else {{
                    console.error("Request to creates notes action failed");
                }}
            }}
            """
        )


def _setup_deck_delete_hook() -> None:
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
