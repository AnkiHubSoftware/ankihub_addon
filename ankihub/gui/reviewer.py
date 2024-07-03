"""Modifies Anki's reviewer UI (aqt.reviewer)."""

import json
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Tuple

import aqt
from anki.cards import Card
from aqt.browser import Browser
from aqt.gui_hooks import (
    reviewer_did_show_question,
    webview_did_receive_js_message,
    webview_will_set_content,
)
from aqt.reviewer import Reviewer, ReviewerBottomBar
from aqt.theme import theme_manager
from aqt.utils import openLink
from aqt.webview import WebContent
from jinja2 import Template

from ..db import ankihub_db
from ..feature_flags import feature_flags
from ..gui.menu import AnkiHubLogin
from ..settings import ANKING_DECK_ID, config, url_view_note
from .operations.scheduling import suspend_notes, unsuspend_notes
from .utils import using_qt5

VIEW_NOTE_PYCMD = "ankihub_view_note"
VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

ANKIHUB_AI_JS_PATH = Path(__file__).parent / "web/ankihub_ai.js"
AI_INVALID_AUTH_TOKEN_PYCMD = "ankihub_ai_invalid_auth_token"

OPEN_BROWSER_PYCMD = "ankihub_open_browser"
UNSUSPEND_NOTES_PYCMD = "ankihub_unsuspend_notes"
SUSPEND_NOTES_PYCMD = "ankihub_suspend_notes"
CLOSE_ANKIHUB_CHATBOT_PYCMD = "ankihub_close_chatbot"


def setup():
    """Sets up the AnkiHub AI chatbot. Adds the "View on AnkiHub" button to the reviewer toolbar."""
    reviewer_did_show_question.append(_add_or_refresh_view_note_button)

    if not using_qt5():
        webview_will_set_content.append(_add_ankihub_ai_js_to_reviewer_web_content)
        reviewer_did_show_question.append(_notify_ankihub_ai_of_card_change)
        config.token_change_hook.append(_set_token_for_ankihub_ai_js)

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

    if not feature_flags.chatbot:
        return

    reviewer: Reviewer = context
    if not ankihub_db.ankihub_did_for_anki_nid(reviewer.card.nid) == ANKING_DECK_ID:
        # Only show the AI chatbot for cards in the AnKing deck
        return

    template_vars = {
        "KNOX_TOKEN": config.token(),
        "APP_URL": config.app_url,
        "ENDPOINT_PATH": "ai/chatbot",
        "QUERY_PARAMETERS": "is_on_anki=true",
        "THEME": _ankihub_theme(),
    }
    js = Template(ANKIHUB_AI_JS_PATH.read_text()).render(template_vars)

    web_content.body += f"<script>{js}</script>"


def _ankihub_theme() -> str:
    """Returns the theme that AnkiHub should use based on the current Anki theme."""
    return "dark" if theme_manager.night_mode else "light"


def _notify_ankihub_ai_of_card_change(card: Card) -> None:
    if not feature_flags.chatbot:
        return

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(card.nid)
    js = _wrap_with_ankihubAI_check(f"ankihubAI.cardChanged('{ah_nid}');")
    aqt.mw.reviewer.web.eval(js)


def _set_token_for_ankihub_ai_js() -> None:
    if not feature_flags.chatbot:
        return

    js = _wrap_with_ankihubAI_check(f"ankihubAI.setToken('{config.token()}');")
    aqt.mw.reviewer.web.eval(js)


def _wrap_with_ankihubAI_check(js: str) -> str:
    """Wraps the given JavaScript code to only run if the AnkiHub AI object is defined."""
    return f"if (typeof ankihubAI !== 'undefined') {{ {js} }}"


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles the "View on AnkiHub" button click by opening the AnkiHub note in the browser."""
    if message == VIEW_NOTE_PYCMD:
        assert isinstance(context, ReviewerBottomBar)
        anki_nid = context.reviewer.card.nid
        ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(anki_nid)
        view_note_url = f"{url_view_note()}{ankihub_nid}"
        openLink(view_note_url)

        return (True, None)
    elif message == AI_INVALID_AUTH_TOKEN_PYCMD:
        assert isinstance(context, Reviewer)
        AnkiHubLogin.display_login()

        return (True, None)
    elif message.startswith(OPEN_BROWSER_PYCMD):
        kwargs = _parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds", [])

        browser: Browser = aqt.dialogs.open("Browser", aqt.mw)

        if ah_nids:
            search_string = f"ankihub_id:{' or ankihub_id:'.join(ah_nids)}"
            browser.search_for(search_string)

        return (True, None)
    elif message.startswith(SUSPEND_NOTES_PYCMD):
        kwargs = _parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds")
        if ah_nids:
            suspend_notes(ah_nids)

        return (True, None)
    elif message.startswith(UNSUSPEND_NOTES_PYCMD):
        kwargs = _parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds")
        if ah_nids:
            unsuspend_notes(ah_nids)
    elif message == CLOSE_ANKIHUB_CHATBOT_PYCMD:
        assert isinstance(context, Reviewer), context
        js = _wrap_with_ankihubAI_check("ankihubAI.hideIframe();")
        context.web.eval(js)

        return (True, None)

    return handled


def _parse_js_message_kwargs(message: str) -> Dict[str, Any]:
    if " " in message:
        _, kwargs_json = message.split(" ", maxsplit=1)
        return json.loads(kwargs_json)
    else:
        return {}
