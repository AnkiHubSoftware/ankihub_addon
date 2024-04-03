"""Modifies the Anki deck browser (aqt.deckbrowser)."""

from typing import Any

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt.deckbrowser import DeckBrowser, DeckBrowserContent
from aqt.gui_hooks import (
    deck_browser_will_render_content,
    webview_did_receive_js_message,
)
from aqt.qt import QUrl
from aqt.webview import AnkiWebView

from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import config
from .utils import ask_user

FLASHCARD_SELCTOR_PYCMD = "ankihub_flashcard_selector"
FLASHCARD_SELECTOR_DECK_ID = 1708540327330


def setup() -> None:
    _setup_deck_delete_hook()
    _setup_flashcard_selector_button()


def _setup_flashcard_selector_button() -> None:
    deck_browser_will_render_content.append(_add_flashcard_selector_button)
    webview_did_receive_js_message.append(_handle_flashcard_selector_button_click)


def _add_flashcard_selector_button(
    browser: DeckBrowser, content: DeckBrowserContent
) -> None:
    content.tree += f"""
        <script type="text/javascript">
            var button = document.createElement("button");
            button.innerHTML = "Add flashcards";

            button.addEventListener("click", function() {{
              pycmd("{FLASHCARD_SELCTOR_PYCMD}");
            }});

            var deckElement = document.querySelector(".deck[id='{FLASHCARD_SELECTOR_DECK_ID}']");
            deckElement.appendChild(button);
        </script>
    """


def _handle_flashcard_selector_button_click(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if not isinstance(context, DeckBrowser):
        return handled

    if message == FLASHCARD_SELCTOR_PYCMD:
        _open_flashcard_selector()
        return (True, None)
    else:
        return handled


def _open_flashcard_selector() -> None:
    webview = AnkiWebView()
    webview.set_open_links_externally(False)
    webview.load_url(QUrl("https://app.ankihub.net/explore/"))

    # Open the webview window in the center of the main window
    # ... Calculate the center point of the main window
    main_window_geometry = aqt.mw.geometry()
    center_x = main_window_geometry.x() + main_window_geometry.width() // 2
    center_y = main_window_geometry.y() + main_window_geometry.height() // 2

    # ... Adjust for the size of the webview window
    webview_x = center_x - webview.width() // 2
    webview_y = center_y - webview.height() // 2

    # Set the position of the webview window
    webview.move(webview_x, webview_y)
    webview.show()


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
