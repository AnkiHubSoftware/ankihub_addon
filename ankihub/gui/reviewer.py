"""Modifies to Anki's reviewer UI (aqt.reviewer)."""
from textwrap import dedent
from typing import Any, Tuple

import aqt
from anki import cards
from aqt import gui_hooks, reviewer, utils

from ..db import ankihub_db
from ..settings import url_view_note

VIEW_NOTE_PYCMD = "ankihub_view_note"
VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"


def setup():
    """Adds the "View on AnkiHub" button to the reviewer toolbar."""
    gui_hooks.reviewer_did_show_question.append(_add_or_refresh_view_note_button)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)


def _add_or_refresh_view_note_button(card: cards.Card):
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


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles the "View on AnkiHub" button click by opening the AnkiHub note in the browser."""
    if message == VIEW_NOTE_PYCMD:
        assert isinstance(context, reviewer.ReviewerBottomBar)
        anki_nid = context.reviewer.card.nid
        ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(anki_nid)
        view_note_url = f"{url_view_note()}{ankihub_nid}"
        utils.openLink(view_note_url)

        return (True, None)

    return handled
