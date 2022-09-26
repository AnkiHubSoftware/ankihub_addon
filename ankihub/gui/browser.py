from concurrent.futures import Future
from pprint import pformat
from typing import Optional, Sequence

from anki.collection import BrowserColumns
from aqt import mw
from aqt.browser import Browser, CellRow, Column, ItemId
from aqt.gui_hooks import (
    browser_did_fetch_columns,
    browser_did_fetch_row,
    browser_will_show,
    browser_will_show_context_menu,
)
from aqt.qt import QMenu
from aqt.utils import showInfo, showText

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError
from ..settings import AnkiHubCommands
from ..suggestions import BulkNoteSuggestionsResult, suggest_notes_in_bulk
from .suggestion_dialog import SuggestionDialog

browser: Optional[Browser] = None


def on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    menu = context_menu
    menu.addSeparator()
    menu.addAction(
        "AnkiHub: Bulk suggest notes",
        lambda: on_bulk_notes_suggest_action(browser),
    )


def on_bulk_notes_suggest_action(browser: Browser) -> None:
    selected_nids = browser.selected_notes()
    notes = [mw.col.get_note(selected_nid) for selected_nid in selected_nids]

    if not (dialog := SuggestionDialog(command=AnkiHubCommands.CHANGE)).exec():
        return

    mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            notes,
            auto_accept=dialog.auto_accept(),
            change_type=dialog.change_type(),
            comment=dialog.comment(),
        ),
        on_done=lambda future: on_suggest_notes_in_bulk_done(future, browser),
        parent=browser,
    )


def on_suggest_notes_in_bulk_done(future: Future, browser: Browser) -> None:
    try:
        suggestions_result: BulkNoteSuggestionsResult = future.result()
    except AnkiHubRequestError as e:
        if e.response.status_code != 403:
            raise e

        msg = (
            "You are not allowed to create suggestion for all selected notes.<br>"
            "Are you subscribed to the AnkiHub deck(s) these notes are from?<br><br>"
            "You can only submit changes without a review if you are an owner or maintainer of the deck."
        )
        showInfo(msg, parent=browser)
        return

    LOGGER.debug("Created note suggestions in bulk.")
    LOGGER.debug(f"errors_by_nid:\n{pformat(suggestions_result.errors_by_nid)}")

    msg_about_created_suggestions = (
        f"Submitted {suggestions_result.change_note_suggestions_count} change note suggestion(s).\n"
        f"Submitted {suggestions_result.new_note_suggestions_count} new note suggestion(s) to.\n\n\n"
    )

    notes_without_changes = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if "Suggestion fields and tags don't have any changes to the original note"
        in str(errors)
    ]
    msg_about_failed_suggestions = (
        (
            f"Failed to submit suggestions for {len(suggestions_result.errors_by_nid)} note(s).\n"
            "All notes with failed suggestions:\n"
            f'{", ".join(str(nid) for nid in suggestions_result.errors_by_nid.keys())}\n\n'
            f"Notes without changes ({len(notes_without_changes)}):\n"
            f'{", ".join(str(nid) for nid in notes_without_changes)}\n'
        )
        if suggestions_result.errors_by_nid
        else ""
    )

    msg = msg_about_created_suggestions + msg_about_failed_suggestions
    showText(msg, parent=browser)


def on_browser_did_fetch_columns(columns: dict[str, Column]):
    columns["ankihub"] = Column(
        key="ankihub",
        cards_mode_label="AnkiHub",
        notes_mode_label="AnkiHub",
        sorting=BrowserColumns.SORTING_NONE,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )


def on_browser_did_fetch_row(
    item_id: ItemId,
    is_notes_mode: bool,
    row: CellRow,
    active_columns: Sequence[str],
):
    global browser

    note = browser.table._state.get_note(item_id)
    for index, key in enumerate(active_columns):
        if key != "ankihub":
            continue

        try:
            if "ankihub_id" in note:
                if note["ankihub_id"]:
                    val = "👑"
                else:
                    val = "👑 (not synced)"
            else:
                val = ""

            row.cells[index].text = val
        except Exception as error:
            row.cells[index].text = f"{error}"


def setup() -> None:
    def store_browser_reference(browser_: Browser) -> None:
        global browser
        browser = browser_

    browser_will_show.append(store_browser_reference)
    browser_did_fetch_columns.append(on_browser_did_fetch_columns)
    browser_did_fetch_row.append(on_browser_did_fetch_row)

    browser_will_show_context_menu.append(on_browser_will_show_context_menu)
