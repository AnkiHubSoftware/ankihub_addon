"""Modifies Anki's reviewer UI (aqt.reviewer)."""

from pathlib import Path
from textwrap import dedent
from typing import Any, Tuple

import aqt
from anki.cards import Card
from aqt.gui_hooks import (
    reviewer_did_show_answer,
    reviewer_did_show_question,
    webview_did_receive_js_message,
    webview_will_set_content,
    reviewer_will_end,
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.webview import WebContent
from jinja2 import Template

from ..db import ankihub_db
from ..gui.menu import AnkiHubLogin
from ..settings import config
from .js_message_handling import VIEW_NOTE_PYCMD
from .utils import get_ah_did_of_deck_or_ancestor_deck, using_qt5

VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

ANKIHUB_AI_JS_PATH = Path(__file__).parent / "web/ankihub_ai.js"
REMOVE_ANKING_BUTTON_JS_PATH = Path(__file__).parent / "web/remove_anking_button.js"

AI_INVALID_AUTH_TOKEN_PYCMD = "ankihub_ai_invalid_auth_token"
CLOSE_ANKIHUB_CHATBOT_PYCMD = "ankihub_close_chatbot"


def setup():
    """Sets up the AnkiHub AI chatbot. Adds the "View on AnkiHub" button to the reviewer toolbar."""
    reviewer_did_show_question.append(_add_or_refresh_view_note_button)
    reviewer_did_show_answer.append(_toggle_split_screen_webview_on_show_answer)
    reviewer_will_end.append(_close_split_screen_webview)

    if not using_qt5():
        webview_will_set_content.append(_add_ankihub_ai_js_to_reviewer_web_content)
        reviewer_did_show_question.append(_notify_ankihub_ai_of_card_change)
        config.token_change_hook.append(_set_token_for_ankihub_ai_js)
        reviewer_did_show_question.append(_remove_anking_button)
        reviewer_did_show_answer.append(_remove_anking_button)

    webview_did_receive_js_message.append(_on_js_message)


def _add_or_refresh_view_note_button(card: Card) -> None:
    """Adds the "View on AnkiHub" button to the reviewer toolbar if it doesn't exist yet,
    or refreshes it if it does exist already."""

    if (
        not aqt.mw.reviewer
        or not aqt.mw.reviewer.bottom
        or not aqt.mw.reviewer.bottom.web
    ):
        return

    html = dedent(
        f"""
        <button id="{VIEW_NOTE_BUTTON_ID}">
            View on AnkiHub
        </button>

        <style>
            #{VIEW_NOTE_BUTTON_ID} {{
                position: absolute;
            }}

            #{VIEW_NOTE_BUTTON_ID}:disabled {{
                color: gray;
                cursor: not-allowed;
                pointer-events: none;
            }}

            /* to not overlap with the answer buttons */
            @media(max-width: 900px) {{
                #{VIEW_NOTE_BUTTON_ID} {{
                    display: none;
                }}
            }}
        </style>
        """
    ).replace(
        "\n", " "
    )  # remove newlines to make insertAdjacentHTML work

    ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    js = dedent(
        f"""
        (function() {{
        if (document.querySelector("#{VIEW_NOTE_BUTTON_ID}") === null) {{
            document.querySelector("#innertable td").insertAdjacentHTML("beforeend", '{html}');
            let button = document.querySelector("#{VIEW_NOTE_BUTTON_ID}");
            button.addEventListener("click", () => {{ pycmd('{VIEW_NOTE_PYCMD}') }})
        }}

        let button = document.querySelector("#{VIEW_NOTE_BUTTON_ID}");
        button.disabled = {"true" if ankihub_nid is None else "false"};
        }})()
        """
    )

    aqt.mw.reviewer.bottom.web.eval(js)



def _add_ankihub_ai_js_to_reviewer_web_content(web_content: WebContent, context):
    """Injects the AnkiHub AI JavaScript into the reviewer web content."""

    if not isinstance(context, Reviewer):
        return

    # feature_flags = config.get_feature_flags()
    # if not feature_flags.get("chatbot", False):
    #     return

    reviewer: Reviewer = context
    ah_did_of_note = ankihub_db.ankihub_did_for_anki_nid(reviewer.card.nid)
    ah_dids_of_note_type = ankihub_db.ankihub_dids_for_note_type(
        reviewer.card.note().mid
    )
    ah_did_of_deck = get_ah_did_of_deck_or_ancestor_deck(
        aqt.mw.col.decks.current()["id"]
    )
    # ah_dids = {ah_did_of_note, ah_did_of_deck, *ah_dids_of_note_type} - {None}
    # if not any(
    #     (
    #         (deck_config := config.deck_config(ah_did))
    #         and deck_config.has_note_embeddings
    #     )
    #     for ah_did in ah_dids
    # ):
    #     return

    template_vars = {
        "KNOX_TOKEN": config.token(),
        "APP_URL": config.app_url,
        "ENDPOINT_PATH": "ai/chatbot",
        "QUERY_PARAMETERS": "is_on_anki=true",
        "THEME": _ankihub_theme(),
    }
    js = Template(ANKIHUB_AI_JS_PATH.read_text()).render(template_vars)

    web_content.body += f"<script>{js}</script>"
    web_content.body += """
        <button id='ankihub-chatbot-button'>Test button</button>
        <script>
            document.getElementById('ankihub-chatbot-button').addEventListener('click', function() {
                pycmd("my_button_clicked")
            });
        </script>
    """


def _ankihub_theme() -> str:
    """Returns the theme that AnkiHub should use based on the current Anki theme."""
    return "dark" if theme_manager.night_mode else "light"


def _notify_ankihub_ai_of_card_change(card: Card) -> None:
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("chatbot", False):
        return

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    js = _wrap_with_ankihubAI_check(f"ankihubAI.cardChanged('{ah_nid}');")
    aqt.mw.reviewer.web.eval(js)


def _remove_anking_button(_: Card) -> None:
    """Removes the AnKing button (provided by the AnKing note types) from the webview if it exists.
    This is necessary because it overlaps with the AnkiHub AI chatbot button."""
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("chatbot", False):
        return

    js = _wrap_with_ankihubAI_check(REMOVE_ANKING_BUTTON_JS_PATH.read_text())
    aqt.mw.reviewer.web.eval(js)


def _set_token_for_ankihub_ai_js() -> None:
    feature_flags = config.get_feature_flags()
    if not feature_flags.get("chatbot", False):
        return

    js = _wrap_with_ankihubAI_check(f"ankihubAI.setToken('{config.token()}');")
    aqt.mw.reviewer.web.eval(js)


def _wrap_with_ankihubAI_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the AnkiHub AI object is defined."""
    return f"if (typeof ankihubAI !== 'undefined') {{ {js} }}"

class _SplitScreenWebViewManager:
    def __init__(self, reviewer: Reviewer):
        self.reviewer = reviewer
        self.splitter = None
        self.splitter_inner = None
        self.web_view = None
        self.web_view_inner = None
        self.is_inner_visible = False

    def create_web_views(self):
        parent_widget = self.reviewer.mw

        if parent_widget is None:
            raise ValueError("Reviewer does not have a parent widget to hold the splitter.")

        self.splitter = aqt.QSplitter()
        
        # Create a QWebEngineProfile with persistent storage
        profile = aqt.QWebEngineProfile("AnkiHubProfile", parent_widget)
        profile.setPersistentStoragePath(str(Path.home() / ".ankihub_profile_test"))
        profile.setPersistentCookiesPolicy(aqt.QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.setHttpUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

        # Create the main web view
        self.web_view = aqt.QWebEngineView()
        self.web_view.setPage(aqt.QWebEnginePage(profile, self.web_view))
        self.web_view.setHtml("<button>Hello</button>")

        # Create the inner web view
        self.web_view_inner = aqt.QWebEngineView()
        self.web_view_inner.setPage(aqt.QWebEnginePage(profile, self.web_view_inner))
        self.web_view_inner.settings().setAttribute(aqt.QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        # self.web_view_inner.setUrl(aqt.QUrl("https://app.ankihub.net"))
        self.web_view_inner.setUrl(aqt.QUrl("https://www.boardsbeyond.com/video/step-1-p/enzymes"))

        # self.web_view_inner.setSizePolicy(aqt.QSizePolicy(aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Fixed))

        # Create a vertical splitter for the inner web views 
        self.splitter_inner = aqt.QSplitter(aqt.Qt.Orientation.Vertical)
        self.splitter_inner.addWidget(self.web_view)
        self.splitter_inner.addWidget(self.web_view_inner)
        self.splitter_inner.setSizes([600, 10000])
        self.splitter_inner.setHandleWidth(0)

        # Assuming parent_widget is a QWidget or has a layout to add the splitter
        layout = parent_widget.layout()
        if layout is None:
            layout = aqt.QVBoxLayout(parent_widget)
            parent_widget.setLayout(layout)
        
        layout.addWidget(self.splitter)
        self.splitter.setSizePolicy(aqt.QSizePolicy(aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Expanding))
        
        widget_index = parent_widget.mainLayout.indexOf(self.reviewer.web)
        parent_widget.mainLayout.removeWidget(self.reviewer.web)

        self.splitter.addWidget(self.reviewer.web)
        self.splitter.addWidget(self.splitter_inner)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([10000, 10000])

        parent_widget.mainLayout.insertWidget(widget_index, self.splitter)
        self.is_inner_visible = True

    def toggle_inner_web_views(self):
        if self.is_inner_visible:
            self.hide_inner_web_views()
        else:
            self.show_inner_web_views()
        
    def show_inner_web_views(self):
        if not self.is_inner_visible:
            self.splitter_inner.show()
            self.is_inner_visible = True
            self.reload_web_view_inner()
            self.change_web_view_html()
        
    def hide_inner_web_views(self):
        if self.is_inner_visible:
            self.splitter_inner.hide()
            self.is_inner_visible = False

    def reload_web_view_inner(self):
        if self.web_view_inner:
            # self.web_view_inner.setUrl(aqt.QUrl("https://app.ankihub.net"))
            self.web_view_inner.setUrl(aqt.QUrl("https://www.boardsbeyond.com/video/step-1-p/enzymes"))

    def change_web_view_html(self):
        if self.web_view:
            self.web_view.setHtml("<button>New Content</button>")


web_view_manager = None

def _toggle_split_screen_webview(reviewer: Reviewer):
    global web_view_manager
    if web_view_manager is None:
        web_view_manager = _SplitScreenWebViewManager(reviewer)
        web_view_manager.create_web_views()
    else:
        web_view_manager.toggle_inner_web_views()
        
def _close_split_screen_webview():
    global web_view_manager
    if web_view_manager is not None:
        web_view_manager.hide_inner_web_views()
        
def _toggle_split_screen_webview_on_show_answer(card: Card):
    reviewer = aqt.mw.reviewer
    _toggle_split_screen_webview(reviewer)

def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles messages sent from JavaScript code."""    
    if message == AI_INVALID_AUTH_TOKEN_PYCMD:
        assert isinstance(context, Reviewer)
        AnkiHubLogin.display_login()

        return (True, None)
    elif message == CLOSE_ANKIHUB_CHATBOT_PYCMD:
        assert isinstance(context, Reviewer), context
        js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
        context.web.eval(js)

        return (True, None)
    
    # TODO define the message as a constant
    elif message == "my_button_clicked":
        _toggle_split_screen_webview(context)
        return (True, None)
    
    return handled
