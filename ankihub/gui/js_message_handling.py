"""Handles messages sent from JavaScript code which are useful in multiple places
(instead of being specific to a single module)."""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aqt
from anki.consts import QUEUE_TYPE_SUSPENDED
from anki.utils import ids2str
from aqt.browser import Browser
from aqt.gui_hooks import webview_did_receive_js_message
from aqt.utils import openLink, tooltip
from aqt.webview import AnkiWebView
from jinja2 import Template

from ..db import ankihub_db
from ..gui.terms_dialog import TermsAndConditionsDialog
from ..settings import url_view_note
from .config_dialog import get_config_dialog_manager
from .operations.scheduling import suspend_notes, unsuspend_notes

VIEW_NOTE_PYCMD = "ankihub_view_note"
VIEW_NOTE_BUTTON_ID = "ankihub-view-note-button"

OPEN_BROWSER_PYCMD = "ankihub_open_browser"
UNSUSPEND_NOTES_PYCMD = "ankihub_unsuspend_notes"
SUSPEND_NOTES_PYCMD = "ankihub_suspend_notes"
GET_NOTE_SUSPENSION_STATES_PYCMD = "ankihub_get_note_suspension_states"
COPY_TO_CLIPBOARD_PYCMD = "ankihub_copy_to_clipboard"
OPEN_LINK_PYCMD = "ankihub_open_link"
TERMS_AGREEMENT_NOT_ACCEPTED = "terms_agreement_not_accepted"
TERMS_AGREEMENT_ACCEPTED = "terms_agreement_accepted"

OPEN_CONFIG_PYCMD = "ankihub_open_config"

POST_MESSAGE_TO_ANKIHUB_JS_PATH = (
    Path(__file__).parent / "web/post_message_to_ankihub_js.js"
)


def setup():
    webview_did_receive_js_message.append(_on_js_message)


def _on_js_message(handled: Tuple[bool, Any], message: str, context: Any) -> Any:
    """Handles messages sent from JavaScript code."""
    if message == VIEW_NOTE_PYCMD:
        anki_nid = context.reviewer.card.nid
        ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(anki_nid)
        view_note_url = f"{url_view_note()}{ankihub_nid}"
        openLink(view_note_url)

        return (True, None)
    elif message == TERMS_AGREEMENT_NOT_ACCEPTED:
        from ..gui.overview import FlashCardSelectorDialog
        from .reviewer import reviewer_sidebar

        TermsAndConditionsDialog.display(parent=aqt.mw)
        if reviewer_sidebar:

            reviewer_sidebar.set_needs_to_accept_terms(True)
            reviewer_sidebar.close_sidebar()

            if aqt.mw.reviewer:
                js = "ankihubReviewerButtons.unselectAllButtons()"
                aqt.mw.reviewer.web.eval(js)

        if FlashCardSelectorDialog.dialog:
            FlashCardSelectorDialog.dialog.close()

        return (True, None)
    elif message == TERMS_AGREEMENT_ACCEPTED:
        from .reviewer import reviewer_sidebar

        TermsAndConditionsDialog.hide()
        if reviewer_sidebar:
            reviewer_sidebar.set_needs_to_accept_terms(False)
            reviewer_sidebar.access_last_accessed_url()

        return (True, None)

    elif message.startswith(OPEN_BROWSER_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        search_string = kwargs.get("searchString", "")
        ah_nids = kwargs.get("noteIds", [])

        if ah_nids:
            ah_nids_to_anki_nids = ankihub_db.ankihub_nids_to_anki_nids(ah_nids)
            anki_nids = [
                anki_nid for anki_nid in ah_nids_to_anki_nids.values() if anki_nid
            ]
            search_string = f"nid:{','.join(map(str, anki_nids))}"

        browser: Browser = aqt.dialogs.open("Browser", aqt.mw)
        if search_string:
            browser.search_for(search_string)

        return (True, None)
    elif message.startswith(SUSPEND_NOTES_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds")
        if ah_nids:
            suspend_notes(
                ah_nids,
                on_done=lambda: tooltip("AnkiHub: Note(s) suspended", parent=aqt.mw),
            )

        return (True, None)
    elif message.startswith(UNSUSPEND_NOTES_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds")
        if ah_nids:
            unsuspend_notes(
                ah_nids,
                on_done=lambda: tooltip("AnkiHub: Note(s) unsuspended", parent=aqt.mw),
            )
    elif message.startswith(GET_NOTE_SUSPENSION_STATES_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        ah_nids = kwargs.get("noteIds")
        note_suspension_states = _get_note_suspension_states(ah_nids)

        _post_message_to_ankihub_js(
            message={"noteSuspensionStates": note_suspension_states}, web=context.web
        )
        return (True, None)
    elif message.startswith(COPY_TO_CLIPBOARD_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        content = kwargs.get("content")
        if content:
            aqt.mw.app.clipboard().setText(content)

        return (True, None)
    elif message.startswith(OPEN_LINK_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        url = kwargs.get("url")
        if url:
            openLink(url)

        return (True, None)
    elif message == OPEN_CONFIG_PYCMD:
        get_config_dialog_manager().open_config()
        return (True, None)

    return handled


def parse_js_message_kwargs(message: str) -> Dict[str, Any]:
    if " " in message:
        _, kwargs_json = message.split(" ", maxsplit=1)
        return json.loads(kwargs_json)
    else:
        return {}


def _post_message_to_ankihub_js(message, web: AnkiWebView) -> None:
    """Posts a message to a message listener on an AnkiHub web page."""
    args = {
        "MESSAGE_JSON": json.dumps(message),
    }
    js = Template(POST_MESSAGE_TO_ANKIHUB_JS_PATH.read_text()).render(args)
    web.eval(js)


def _get_note_suspension_states(ah_nids: List[str]) -> Dict[str, bool]:
    """Returns a mapping of AnkiHub note IDs (as strings) to whether they are suspended or not.
    A note is considered unsuspended if at least one of its cards is unsuspended.
    If the note is not found in Anki, it will be missing from the returned mapping."""
    ah_nids_to_anki_nids = ankihub_db.ankihub_nids_to_anki_nids(
        [uuid.UUID(ah_nid) for ah_nid in ah_nids]
    )
    ah_nids_to_anki_nids = {
        ah_nid: anki_nid
        for ah_nid, anki_nid in ah_nids_to_anki_nids.items()
        if anki_nid
    }
    if not ah_nids_to_anki_nids:
        return {}

    unsuspended_anki_nids = set(
        aqt.mw.col.db.list(
            f"""
            SELECT DISTINCT nid FROM cards
            WHERE nid IN {ids2str(ah_nids_to_anki_nids.values())} AND queue != {QUEUE_TYPE_SUSPENDED}
            """
        )
    )
    return {
        str(ah_nid): anki_nid not in unsuspended_anki_nids
        for ah_nid, anki_nid in ah_nids_to_anki_nids.items()
    }
