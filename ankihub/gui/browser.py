import uuid
from abc import abstractmethod
from concurrent.futures import Future
from pprint import pformat
from typing import Callable, List, Optional, Sequence

from anki.collection import BrowserColumns
from anki.notes import Note
from aqt import mw
from aqt.browser import Browser, CellRow, Column, ItemId, SearchContext
from aqt.gui_hooks import (
    browser_did_fetch_columns,
    browser_did_fetch_row,
    browser_did_search,
    browser_will_search,
    browser_will_show,
    browser_will_show_context_menu,
)
from aqt.qt import QMenu
from aqt.utils import showInfo, showText

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError
from ..db import AnkiHubDB, attach_ankihub_db_to_anki_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, AnkiHubCommands
from ..suggestions import BulkNoteSuggestionsResult, suggest_notes_in_bulk
from .suggestion_dialog import SuggestionDialog

browser: Optional[Browser] = None
db: AnkiHubDB = AnkiHubDB()


def on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    menu = context_menu

    menu.addSeparator()

    menu.addAction(
        "AnkiHub: Bulk suggest notes",
        lambda: on_bulk_notes_suggest_action(browser),
    )

    # setup copy ankihub_id to clipboard action
    selected_nids = browser.selected_notes()
    notes = [mw.col.get_note(selected_nid) for selected_nid in selected_nids]

    copy_ankihub_id_action = menu.addAction(
        "AnkiHub: Copy AnkiHub ID to clipboard",
        lambda: mw.app.clipboard().setText(notes[0]["ankihub_id"]),
    )

    if not (
        len(notes) == 1 and "ankihub_id" in (note := notes[0]) and note["ankihub_id"]
    ):
        copy_ankihub_id_action.setDisabled(True)


def on_bulk_notes_suggest_action(browser: Browser) -> None:
    selected_nids = browser.selected_notes()
    notes = [mw.col.get_note(selected_nid) for selected_nid in selected_nids]

    if len(notes) > 500:
        msg = "Please select less than 500 notes at a time for bulk suggestions.<br>"
        showInfo(msg, parent=browser)
        return

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


class CustomColumn:
    builtin_column: Column
    order_by_str: Optional[str] = None
    create_sort_table: Optional[Callable] = None

    def on_browser_did_fetch_row(
        self,
        item_id: ItemId,
        row: CellRow,
        active_columns: Sequence[str],
    ) -> None:
        if (
            index := active_columns.index(self.key)
            if self.key in active_columns
            else None
        ) is None:
            return

        note = browser.table._state.get_note(item_id)
        try:
            value = self._display_value(note)
            row.cells[index].text = value
        except Exception as error:
            row.cells[index].text = str(error)

    @property
    def key(self):
        return self.builtin_column.key

    @abstractmethod
    def _display_value(
        self,
        note: Note,
    ) -> str:
        raise NotImplementedError


class AnkiHubIdColumn(CustomColumn):

    builtin_column = Column(
        key="ankihub_id",
        cards_mode_label="AnkiHub ID",
        notes_mode_label="AnkiHub ID",
        sorting=BrowserColumns.SORTING_NONE,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" in note:
            if note["ankihub_id"]:
                return note["ankihub_id"]
            else:
                return "ID Pending"
        else:
            return "Not AnkiHub Note Type"


class EditedAfterSyncColumn(CustomColumn):
    builtin_column = Column(
        key="edited_after_sync",
        cards_mode_label="AnkiHub: Modified After Sync",
        notes_mode_label="AnkiHub: Modified After Sync",
        sorting=BrowserColumns.SORTING_ASCENDING,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" not in note or not note["ankihub_id"]:
            return "N/A"

        last_sync = db.last_sync(uuid.UUID(note["ankihub_id"]))
        if last_sync is None:
            # The sync_mod value can be None if the note was synced with an early version of the AnkiHub add-on
            return "Unknown"

        return "Yes" if note.mod > last_sync else "No"

    def create_sort_table(self) -> None:
        attach_ankihub_db_to_anki_db()

        mw.col.db.execute("DROP TABLE IF EXISTS temp")
        mw.col.db.execute(
            "CREATE TABLE temp (anki_id INTEGER PRIMARY KEY, mod INTEGER)"
        )
        mw.col.db.execute(
            "INSERT INTO temp SELECT anki_note_id, mod FROM ankihub_db.notes"
        )

    def order_by_str(self) -> str:
        ids_of_ankihub_models = [
            model["id"]
            for model in mw.col.models.all()
            if any(
                field["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME for field in model["flds"]
            )
        ]
        return (
            "(select n.mod > temp.mod from temp where temp.anki_id = n.id limit 1), "
            f"(n.mid in ({', '.join(map(str, ids_of_ankihub_models))}))"
        )


custom_columns: List[CustomColumn] = [AnkiHubIdColumn(), EditedAfterSyncColumn()]


def on_browser_did_fetch_columns(columns: dict[str, Column]):
    for column in custom_columns:
        columns[column.key] = column.builtin_column


def on_browser_did_fetch_row(
    item_id: ItemId,
    is_notes_mode: bool,
    row: CellRow,
    active_columns: Sequence[str],
) -> None:
    for column in custom_columns:
        column.on_browser_did_fetch_row(
            item_id=item_id,
            row=row,
            active_columns=active_columns,
        )


def on_browser_will_search(ctx: SearchContext):
    if not isinstance(ctx.order, Column):
        return

    custom_column: CustomColumn = next(
        (c for c in custom_columns if c.builtin_column.key == ctx.order.key), None
    )
    if custom_column is None:
        return

    ctx.order = custom_column.order_by_str()

    # If this column relies on a temporary table for sorting, build it now
    if custom_column.create_sort_table:
        custom_column.create_sort_table()


def on_browser_did_search(ctx: SearchContext):
    pass


def setup() -> None:
    def store_browser_reference(browser_: Browser) -> None:
        global browser
        browser = browser_

    browser_will_show.append(store_browser_reference)
    browser_did_fetch_columns.append(on_browser_did_fetch_columns)
    browser_did_fetch_row.append(on_browser_did_fetch_row)
    browser_will_search.append(on_browser_will_search)
    browser_did_search.append(on_browser_did_search)

    browser_will_show_context_menu.append(on_browser_will_show_context_menu)
