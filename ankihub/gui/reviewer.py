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
)
from aqt.reviewer import Reviewer
from aqt.theme import theme_manager
from aqt.webview import WebContent
from jinja2 import Template

from ..db import ankihub_db
from ..feature_flags import feature_flags
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

    if not feature_flags.chatbot:
        return

    reviewer: Reviewer = context
    ah_did_of_note = ankihub_db.ankihub_did_for_anki_nid(reviewer.card.nid)
    ah_dids_of_note_type = ankihub_db.ankihub_dids_for_note_type(
        reviewer.card.note().mid
    )
    ah_did_of_deck = get_ah_did_of_deck_or_ancestor_deck(
        aqt.mw.col.decks.current()["id"]
    )
    ah_dids = {ah_did_of_note, ah_did_of_deck, *ah_dids_of_note_type} - {None}
    if not any(
        (
            (deck_config := config.deck_config(ah_did))
            and deck_config.has_note_embeddings
        )
        for ah_did in ah_dids
    ):
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


def _remove_anking_button(_: Card) -> None:
    """Removes the AnKing button (provided by the AnKing note types) from the webview if it exists.
    This is necessary because it overlaps with the AnkiHub AI chatbot button."""
    if not feature_flags.chatbot:
        return

    js = _wrap_with_ankihubAI_check(REMOVE_ANKING_BUTTON_JS_PATH.read_text())
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
    return handled
