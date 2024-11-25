"""Modifies Anki's reviewer UI (aqt.reviewer)."""

from pathlib import Path
from textwrap import dedent
from typing import Any, Optional, Tuple

import aqt
import aqt.webview
from anki.cards import Card
from aqt.gui_hooks import (
    reviewer_did_show_answer,
    reviewer_did_show_question,
    reviewer_will_end,
    webview_did_receive_js_message,
    webview_will_set_content,
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.webview import WebContent
from jinja2 import Template

from .. import LOGGER
from ..db import ankihub_db
from ..gui.menu import AnkiHubLogin
from ..gui.webview import AuthenticationRequestInterceptor, CustomWebPage  # noqa: F401
from ..settings import config
from .js_message_handling import VIEW_NOTE_PYCMD, parse_js_message_kwargs
from .utils import get_ah_did_of_deck_or_ancestor_deck, using_qt5

VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

ANKIHUB_AI_JS_PATH = Path(__file__).parent / "web/ankihub_ai.js"
ANKIHUB_AI_OLD_JS_PATH = Path(__file__).parent / "web/ankihub_ai_old.js"
REVIEWER_BUTTONS_JS_PATH = Path(__file__).parent / "web/reviewer_buttons.js"
REMOVE_ANKING_BUTTON_JS_PATH = Path(__file__).parent / "web/remove_anking_button.js"
MH_INTEGRATION_TABS_TEMPLATE_PATH = (
    Path(__file__).parent / "web/mh_integration_tabs.html"
)

INVALID_AUTH_TOKEN_PYCMD = "ankihub_invalid_auth_token"
REVIEWER_BUTTON_TOGGLED_PYCMD = "ankihub_reviewer_button_toggled"
CLOSE_ANKIHUB_CHATBOT_PYCMD = "ankihub_close_chatbot"
OPEN_SPLIT_SCREEN_PYCMD = "ankihub_open_split_screen"
LOAD_URL_IN_SIDEBAR_PYCMD = "ankihub_load_url_in_sidebar"


class SplitScreenWebViewManager:
    def __init__(self, reviewer: Reviewer, urls_list):
        self.reviewer = reviewer
        self.splitter: Optional[aqt.QSplitter] = None
        self.container: Optional[aqt.QWidget] = None
        self.webview: Optional[aqt.webview.AnkiWebView] = None
        self.header_webview: Optional[aqt.webview.AnkiWebView] = None
        self.current_active_url = urls_list[0]["url"]
        self.urls_list = urls_list
        self._setup_webview()

    def _setup_webview(self):
        parent_widget = self.reviewer.mw

        if parent_widget is None:
            raise ValueError(
                "Reviewer does not have a parent widget to hold the splitter."
            )

        self.splitter = aqt.QSplitter()
        self.container = aqt.QWidget()
        container_layout = aqt.QVBoxLayout()
        self.container.setLayout(container_layout)

        # Create a QWebEngineProfile with persistent storage
        profile = aqt.QWebEngineProfile("AnkiHubProfile", parent_widget)
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"
        )

        # Create the web views
        self.webview = aqt.webview.AnkiWebView()
        self.webview.setPage(CustomWebPage(profile, self.webview._onBridgeCmd))
        self.webview.setUrl(aqt.QUrl(self.urls_list[0]["url"]))
        self.header_webview = aqt.webview.AnkiWebView()
        self.header_webview.setSizePolicy(
            aqt.QSizePolicy(
                aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Fixed
            )
        )
        aqt.qconnect(self.webview.loadFinished, self._inject_header)
        container_layout.addWidget(self.header_webview)
        container_layout.addWidget(self.webview)
        self.container.hide()

        # Interceptor that will add the token to the request
        interceptor = AuthenticationRequestInterceptor(self.webview)
        self.webview.page().profile().setUrlRequestInterceptor(interceptor)

        layout = parent_widget.layout()
        if layout is None:
            layout = aqt.QVBoxLayout(parent_widget)
            parent_widget.setLayout(layout)

        layout.addWidget(self.splitter)
        self.splitter.setSizePolicy(
            aqt.QSizePolicy(
                aqt.QSizePolicy.Policy.Expanding, aqt.QSizePolicy.Policy.Expanding
            )
        )

        widget = self.reviewer.web
        # For compatibility with other add-ons that add a side panel too (e.g. AMBOSS)
        if isinstance(self.reviewer.web.parentWidget(), aqt.QSplitter):
            widget = self.reviewer.web.parentWidget()
        widget_index = parent_widget.mainLayout.indexOf(widget)
        parent_widget.mainLayout.removeWidget(widget)
        self.splitter.addWidget(widget)
        self.splitter.addWidget(self.container)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([10000, 10000])

        parent_widget.mainLayout.insertWidget(widget_index, self.splitter)

    def toggle_split_screen(self):
        if self.container.isVisible():
            self.close_split_screen()
        else:
            self.open_split_screen()

    def open_split_screen(self):
        if not self.container.isVisible():
            self.container.show()

    def close_split_screen(self):
        self.container.hide()

    def _inject_header(self, ok: bool):
        if not ok:
            LOGGER.error("Failed to load page.")  # pragma: no cover
            return  # pragma: no cover
        html_template = Template(MH_INTEGRATION_TABS_TEMPLATE_PATH.read_text()).render(
            {
                "tabs": self.urls_list,
                "current_active_tab_url": self.current_active_url,
                "page_title": "Boards&Beyond viewer",
            }
        )
        self.header_webview.setHtml(html_template)
        self.header_webview.adjustHeightToFit()

    def set_webview_url(self, url):
        if self.webview:
            self.webview.setUrl(aqt.QUrl(url))
            self.current_active_url = url


split_screen_webview_manager: Optional[SplitScreenWebViewManager] = None


def setup():
    """Sets up the AnkiHub AI chatbot. Adds the "View on AnkiHub" button to the reviewer toolbar."""
    reviewer_did_show_question.append(_add_or_refresh_view_note_button)

    if not using_qt5():
        webview_will_set_content.append(_add_ankihub_ai_and_sidebar_and_buttons)
        reviewer_did_show_question.append(_notify_ankihub_ai_of_card_change)
        config.token_change_hook.append(_set_token_for_ankihub_ai_js)
        reviewer_did_show_question.append(_remove_anking_button)
        reviewer_did_show_answer.append(_remove_anking_button)

    webview_did_receive_js_message.append(_on_js_message)
    reviewer_will_end.append(_close_split_screen_webview)


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


def _add_ankihub_ai_and_sidebar_and_buttons(web_content: WebContent, context):
    if not isinstance(context, Reviewer):
        return

    feature_flags = config.get_feature_flags()
    if not (feature_flags.get("mh_integration") or feature_flags.get("chatbot")):
        return

    if not _related_ah_deck_has_note_embeddings(context):
        return

    ah_ai_template_vars = {
        "KNOX_TOKEN": config.token(),
        "APP_URL": config.app_url,
        "ENDPOINT_PATH": "ai/chatbot",
        "QUERY_PARAMETERS": "is_on_anki=true",
        "THEME": _ankihub_theme(),
    }
    if feature_flags.get("mh_integration"):
        global split_screen_webview_manager
        if not split_screen_webview_manager:
            # TODO: Replace with the actual URLs
            urls_list = [
                {
                    "url": "https://www.google.com",
                    "title": "Google",
                },
                {"url": "https://www.bing.com", "title": "Bing"},
            ]
            split_screen_webview_manager = SplitScreenWebViewManager(context, urls_list)

        ankihub_ai_js = Template(ANKIHUB_AI_JS_PATH.read_text()).render(
            ah_ai_template_vars
        )
        web_content.body += f"<script>{ankihub_ai_js}</script>"

        reivewer_button_js = Template(REVIEWER_BUTTONS_JS_PATH.read_text()).render(
            {
                "THEME": _ankihub_theme(),
            }
        )
        web_content.body += f"<script>{reivewer_button_js}</script>"
    else:
        ankihub_ai_old_js = Template(ANKIHUB_AI_OLD_JS_PATH.read_text()).render(
            ah_ai_template_vars
        )
        web_content.body += f"<script>{ankihub_ai_old_js}</script>"


def _related_ah_deck_has_note_embeddings(reviewer: Reviewer) -> bool:
    ah_did_of_note = ankihub_db.ankihub_did_for_anki_nid(reviewer.card.nid)
    ah_dids_of_note_type = ankihub_db.ankihub_dids_for_note_type(
        reviewer.card.note().mid
    )
    ah_did_of_deck = get_ah_did_of_deck_or_ancestor_deck(
        aqt.mw.col.decks.current()["id"]
    )
    ah_dids = {ah_did_of_note, ah_did_of_deck, *ah_dids_of_note_type} - {None}
    return any(
        (
            (deck_config := config.deck_config(ah_did))
            and deck_config.has_note_embeddings
        )
        for ah_did in ah_dids
    )


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


def _wrap_with_reviewer_buttons_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the ankihubReviewerButtons object is defined."""
    return f"if (typeof ankihubReviewerButtons !== 'undefined') {{ {js} }}"


def _close_split_screen_webview():
    if split_screen_webview_manager:
        split_screen_webview_manager.close_split_screen()


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles messages sent from JavaScript code."""
    if message == INVALID_AUTH_TOKEN_PYCMD:
        _handle_invalid_auth_token()

        return (True, None)
    elif message == CLOSE_ANKIHUB_CHATBOT_PYCMD:
        assert isinstance(context, Reviewer), context
        js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
        context.web.eval(js)

        return (True, None)
    elif message.startswith(REVIEWER_BUTTON_TOGGLED_PYCMD):
        assert isinstance(context, Reviewer), context
        kwargs = parse_js_message_kwargs(message)
        button_name = kwargs.get("buttonName")

        if button_name == "chatbot":
            js = _wrap_with_ankihubAI_check("ankihubAI.toggleIframe();")
            context.web.eval(js)
        else:
            # TODO load correct sidebar content (Boards&Beyond, First Aid or AnkiHub Chatbot)
            # depending on the button that was toggled
            split_screen_webview_manager.toggle_split_screen()

        return (True, None)
    elif message.startswith(LOAD_URL_IN_SIDEBAR_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        split_screen_webview_manager.set_webview_url(kwargs["url"])

        return (True, None)

    return handled


def _handle_invalid_auth_token():
    js = _wrap_with_reviewer_buttons_check(
        "ankihubReviewerButtons.unselectAllButtons()"
    )
    aqt.mw.reviewer.web.eval(js)

    AnkiHubLogin.display_login()
