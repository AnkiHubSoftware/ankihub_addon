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
from aqt.qt import QColor, QDialog, QUrl, QVBoxLayout
from aqt.webview import AnkiWebView

from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import ANKING_DECK_ID, config, url_flashcard_selector
from .utils import ask_user

FLASHCARD_SELCTOR_PYCMD = "ankihub_flashcard_selector_open"


def setup() -> None:
    _setup_flashcard_selector_button()
    _setup_deck_delete_hook()


def _setup_flashcard_selector_button() -> None:
    deck_browser_will_render_content.append(_add_flashcard_selector_button)
    webview_did_receive_js_message.append(_handle_flashcard_selector_button_click)


def _add_flashcard_selector_button(
    browser: DeckBrowser, content: DeckBrowserContent
) -> None:
    anking_anki_did = config.deck_config(ANKING_DECK_ID).anki_id
    content.tree += f"""
        <script>
            var button = document.createElement("button");
            button.innerHTML = "Add flashcards";

            button.addEventListener("click", function() {{
              pycmd("{FLASHCARD_SELCTOR_PYCMD}");
            }});

            var deckElement = document.querySelector(".deck[id='{anking_anki_did}']");
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
        # Return True to indicate that the message was handled
        return (True, None)
    else:
        return handled


class FlashCardSelectorDialog(QDialog):
    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(
        self,
    ) -> None:
        self.setMinimumHeight(400)
        self.setMinimumWidth(600)

        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)
        self.web.load_url(QUrl(url_flashcard_selector(ANKING_DECK_ID)))
        self.web.page().setBackgroundColor(QColor("white"))
        self.layout_ = QVBoxLayout()
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.web)

        self.setLayout(self.layout_)


def _open_flashcard_selector() -> None:
    dialog = FlashCardSelectorDialog(aqt.mw)
    dialog.show()


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
