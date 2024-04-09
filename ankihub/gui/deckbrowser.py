"""Modifies the Anki deck browser (aqt.deckbrowser)."""

from typing import Any, Optional

import aqt
from anki.decks import DeckId
from anki.hooks import wrap
from aqt.gui_hooks import deck_browser_did_render, webview_did_receive_js_message
from aqt.qt import QColor, QDialog, QUrl, QVBoxLayout, qconnect
from aqt.webview import AnkiWebView

from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import ANKING_DECK_ID, config, url_flashcard_selector
from .menu import AnkiHubLogin
from .utils import ask_user

FLASHCARD_SELECTOR_OPEN_PYCMD = "ankihub_flashcard_selector_open"
FLASHCARD_SELECTOR_AUTH_FAILED_PYCMD = "ankihub_flashcard_selector_auth_failed"


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

    webview_did_receive_js_message.append(_handle_flashcard_selector_button_click)


def _maybe_add_flashcard_selector_button() -> None:
    """Add the flashcard selector button to the Anking deck if it exists."""
    if not (deck_config := config.deck_config(ANKING_DECK_ID)):
        return

    deck_browser_web: AnkiWebView = aqt.mw.deckBrowser.web
    deck_browser_web.eval(_js_add_flashcard_selector_button(deck_config.anki_id))


def _js_add_flashcard_selector_button(anki_deck_id: DeckId) -> str:
    return f"""
        var button = document.createElement("button");
        button.innerHTML = "Add flashcards";

        button.addEventListener("click", function() {{
          pycmd("{FLASHCARD_SELECTOR_OPEN_PYCMD}");
        }});

        var deckElement = document.querySelector(".deck[id='{anki_deck_id}']");
        deckElement.appendChild(button);
    """


class FlashCardSelectorDialog(QDialog):
    dialog: Optional["FlashCardSelectorDialog"] = None

    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub | Flashcard Selector")
        self.setMinimumHeight(400)
        self.setMinimumWidth(600)

        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)
        self.web.page().setBackgroundColor(QColor("white"))

        self._load_flashcard_selector_page()

        self.layout_ = QVBoxLayout()
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.web)

        self.setLayout(self.layout_)

    def _load_flashcard_selector_page(self) -> None:
        self.web.load_url(QUrl(url_flashcard_selector(ANKING_DECK_ID)))
        qconnect(self.web.loadFinished, self._on_web_load_finished)

    def _on_web_load_finished(self, ok: bool) -> None:
        self.web.eval(
            f"""
            if (window.location.href.includes('login')) {{
                pycmd('{FLASHCARD_SELECTOR_AUTH_FAILED_PYCMD}');
            }}
            """
        )

    @classmethod
    def display(cls, parent: Any) -> "FlashCardSelectorDialog":
        if cls.dialog is None:
            cls.dialog = cls(parent)

        cls.dialog._load_flashcard_selector_page()

        cls.dialog.activateWindow()
        cls.dialog.raise_()
        cls.dialog.show()

        return cls.dialog


def _handle_flashcard_selector_button_click(
    handled: tuple[bool, Any], message: str, context: Any
) -> tuple[bool, Any]:
    if message == FLASHCARD_SELECTOR_OPEN_PYCMD:
        FlashCardSelectorDialog.display(aqt.mw)
        return (True, None)
    elif message == FLASHCARD_SELECTOR_AUTH_FAILED_PYCMD:
        # Close the dialog if the user is not authenticated and prompt them to log in,
        # then they can open the dialog again
        dialog: FlashCardSelectorDialog = FlashCardSelectorDialog.dialog
        dialog.close()

        AnkiHubLogin.display_login()

        return (True, None)
    else:
        return handled


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
