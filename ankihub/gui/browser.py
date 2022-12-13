import re
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import Future
from datetime import datetime
from pprint import pformat
from typing import List, Optional, Sequence

from anki.collection import BrowserColumns, SearchNode
from anki.notes import Note
from anki.utils import ids2str
from aqt import mw
from aqt.browser import (
    Browser,
    CellRow,
    Column,
    ItemId,
    SearchContext,
    SidebarItem,
    SidebarItemType,
    SidebarStage,
)
from aqt.gui_hooks import (
    browser_did_fetch_columns,
    browser_did_fetch_row,
    browser_did_search,
    browser_will_build_tree,
    browser_will_search,
    browser_will_show,
    browser_will_show_context_menu,
)
from aqt.qt import QMenu
from aqt.utils import showInfo, showText, showWarning

from .. import LOGGER
from ..ankihub_client import (
    AnkiHubRequestError,
    SuggestionType,
    suggestion_type_from_str,
)
from ..db import (
    ankihub_db,
    attach_ankihub_db_to_anki_db_connection,
    attached_ankihub_db,
    detach_ankihub_db_from_anki_db_connection,
)
from ..importing import get_fields_protected_by_tags
from ..note_conversion import TAG_FOR_PROTECTING_ALL_FIELDS, TAG_FOR_PROTECTING_FIELDS
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, AnkiHubCommands
from ..suggestions import BulkNoteSuggestionsResult, suggest_notes_in_bulk
from ..utils import note_types_with_ankihub_id_field
from .suggestion_dialog import SuggestionDialog
from .utils import choose_subset

browser: Optional[Browser] = None


class CustomSearchNode(ABC):

    parameter_name: Optional[str] = None

    @classmethod
    def from_parameter_type_and_value(cls, parameter_name, value):
        custom_search_node_types = (
            ModifiedAfterSyncSearchNode,
            UpdatedInTheLastXDaysSearchNode,
            SuggestionTypeSearchNode,
            UpdatedSinceLastReviewSearchNode,
        )
        for custom_search_node_type in custom_search_node_types:
            if custom_search_node_type.parameter_name == parameter_name:
                return custom_search_node_type(value)  # type: ignore

        raise ValueError(f"Unknown custom search parameter: {parameter_name}")

    @abstractmethod
    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        # Filters the given ids to only those that match the custom search node.
        # Expects the ankihub database to be attached to the anki database connection.
        # Ids can be either note ids or card ids.
        pass

    def _retain_ids_where(self, ids: Sequence[ItemId], where: str) -> Sequence[ItemId]:
        # Returns these ids that match the given where clause
        # while joining notes with their corresponding information from the ankihub database.
        # The provided ids can be either note ids or card ids.
        # The anki notes table can be accessed with the table name "notes",
        # cards can be accessed as "cards" and the ankihub notes
        # table can be accessed as "ah_notes".
        global browser

        browser_: Browser = browser
        if browser_.table.is_notes_mode():
            nids = mw.col.db.list(
                "SELECT id FROM notes, ankihub_db.notes as ah_notes "
                "WHERE notes.id = ah_notes.anki_note_id AND "
                f"notes.id IN {ids2str(ids)} AND " + where
            )
            return nids
        else:
            # this approach is faster than joining notes with cards in the query,
            # but maybe this wouldn't be the case if the query were written better
            nids = mw.col.db.list(
                "SELECT DISTINCT nid FROM cards WHERE id IN " + ids2str(ids)
            )
            selected_note_ids = mw.col.db.list(
                "SELECT id FROM notes, ankihub_db.notes as ah_notes "
                "WHERE notes.id = ah_notes.anki_note_id AND "
                f"notes.id IN {ids2str(nids)} AND " + where
            )
            cids = mw.col.db.list(
                "SELECT id FROM cards WHERE nid IN " + ids2str(selected_note_ids)
            )
            return cids


class ModifiedAfterSyncSearchNode(CustomSearchNode):

    parameter_name = "ankihub_modified_after_sync"

    def __init__(self, value: str):
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value == "yes":
            ids = self._retain_ids_where(ids, "notes.mod > ah_notes.mod")
        elif self.value == "no":
            ids = self._retain_ids_where(ids, "notes.mod <= ah_notes.mod")
        else:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. Options are 'yes' and 'no'."
            )

        return ids


class UpdatedInTheLastXDaysSearchNode(CustomSearchNode):

    parameter_name = "ankihub_updated"

    def __init__(self, value: str):
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        try:
            days = int(self.value)
            if days <= 0:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. Must be a positive integer."
            )

        threshold_timestamp = int(datetime.now().timestamp() - (days * 24 * 60 * 60))
        ids = self._retain_ids_where(ids, f"ah_notes.mod > {threshold_timestamp}")

        return ids


class SuggestionTypeSearchNode(CustomSearchNode):

    parameter_name = "ankihub_suggestion_type"

    def __init__(self, value: str):
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        value = self.value.replace("_slash_", "/")
        try:
            suggestion_type_from_str(value)
        except ValueError:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {value}. Must be a suggestion type."
            )

        ids = self._retain_ids_where(ids, f"ah_notes.last_update_type = '{value}'")

        return ids


class UpdatedSinceLastReviewSearchNode(CustomSearchNode):

    parameter_name = "ankihub_updated_since_last_review"

    def __init__(self, value: str):
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value.strip() == "":
            ids = self._retain_ids_where(
                ids,
                """
                ah_notes.mod > (
                    SELECT max(revlog.id) FROM revlog, cards
                    WHERE revlog.cid = cards.id AND cards.nid = notes.id
                ) / 1000
                """,
            )
        else:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. This search parameter takes no values."
            )

        return ids


# stores the custom search nodes for the current search
custom_search_nodes: List[CustomSearchNode] = []


def on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    menu = context_menu

    menu.addSeparator()

    menu.addAction(
        "AnkiHub: Bulk suggest notes",
        lambda: on_bulk_notes_suggest_action(browser),
    )

    menu.addAction(
        "AnkiHub: Protect fields",
        lambda: on_protect_fields_action(browser),
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


def on_protect_fields_action(browser: Browser) -> None:
    nids = browser.selected_notes()
    if len(nids) != 1:
        showInfo("Please select exactly one note.", parent=browser)
        return

    nid = nids[0]

    if ankihub_db.ankihub_id_for_note(nid) is None:
        showInfo("This note is not an AnkiHub note.", parent=browser)
        return

    note = mw.col.get_note(nid)

    fields: List[str] = [
        field for field in note.keys() if field != ANKIHUB_NOTE_TYPE_FIELD_NAME
    ]
    old_fields_protected_by_tags: List[str] = get_fields_protected_by_tags(note)
    new_fields_protected_by_tags = choose_subset(
        "Choose which fields of this note should be protected<br>"
        "from updates.<br><br>"
        "Note: Fields you have protected for the note type<br>"
        "on AnkiHub will be protected automatically.",
        choices=fields,
        current=old_fields_protected_by_tags,
        description_html="This will edit the AnkiHub_Protect tags of the note.",
        parent=browser,
    )

    if set(new_fields_protected_by_tags) == set(fields):
        new_tags_for_protecting_fields = [TAG_FOR_PROTECTING_ALL_FIELDS]
    else:
        new_tags_for_protecting_fields = [
            f"{TAG_FOR_PROTECTING_FIELDS}::{field.replace(' ', '_')}"
            for field in new_fields_protected_by_tags
        ]

    # remove old tags for protecting fields
    note.tags = [
        tag for tag in note.tags if not tag.startswith(TAG_FOR_PROTECTING_FIELDS)
    ]

    # add new tags for protecting fields
    note.tags += new_tags_for_protecting_fields

    note.flush()

    # without this the tags in the browser editor are not updated until you switch away from the note
    browser.table.reset()

    LOGGER.debug(
        f"Updated tags for protecting fields for note {note.id} to protect these fields {new_fields_protected_by_tags}"
    )


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

    def order_by_str(self) -> Optional[str]:
        """Return the SQL string that will be appended after "ORDER BY" to the query that
        fetches the search results when sorting by this column."""
        return None


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
        sorting=BrowserColumns.SORTING_DESCENDING,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" not in note or not note["ankihub_id"]:
            return "N/A"

        last_sync = ankihub_db.last_sync(uuid.UUID(note["ankihub_id"]))
        if last_sync is None:
            # The sync_mod value can be None if the note was synced with an early version of the AnkiHub add-on
            return "Unknown"

        return "Yes" if note.mod > last_sync else "No"

    def order_by_str(self) -> str:
        mids = note_types_with_ankihub_id_field()
        if not mids:
            return None

        return (
            "("
            f"   SELECT n.mod > ah_n.mod from {ankihub_db.database_name}.notes AS ah_n "
            "    WHERE ah_n.anki_note_id = n.id LIMIT 1"
            ") DESC, "
            f"(n.mid IN {ids2str(mids)}) DESC"
        )


class UpdatedSinceLastReviewColumn(CustomColumn):
    builtin_column = Column(
        key="updated_since_last_review",
        cards_mode_label="AnkiHub: Updated Since Last Review",
        notes_mode_label="AnkiHub: Updated Since Last Review",
        sorting=BrowserColumns.SORTING_NONE,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" not in note or not note["ankihub_id"]:
            return "N/A"

        last_sync = ankihub_db.last_sync(uuid.UUID(note["ankihub_id"]))
        if last_sync is None:
            # The sync_mod value can be None if the note was synced with an early version of the AnkiHub add-on
            return "Unknown"

        last_review_ms = mw.col.db.scalar(
            f"""
            SELECT max(revlog.id) FROM revlog, cards
            WHERE {note.id} = cards.nid AND cards.id = revlog.cid
            """,
        )
        if last_review_ms is None:
            return "No"

        last_review = last_review_ms // 1000

        return "Yes" if last_sync > last_review else "No"


custom_columns: List[CustomColumn] = [
    AnkiHubIdColumn(),
    EditedAfterSyncColumn(),
    UpdatedSinceLastReviewColumn(),
]


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
    on_browser_will_search_handle_custom_column_ordering(ctx)
    on_browser_will_search_handle_custom_search_parameters(ctx)


def on_browser_will_search_handle_custom_column_ordering(ctx: SearchContext):
    if not isinstance(ctx.order, Column):
        return

    custom_column: CustomColumn = next(
        (c for c in custom_columns if c.builtin_column.key == ctx.order.key), None
    )
    if custom_column is None:
        return

    attach_ankihub_db_to_anki_db_connection()

    ctx.order = custom_column.order_by_str()


def on_browser_will_search_handle_custom_search_parameters(ctx: SearchContext):
    if not ctx.search:
        return

    global custom_search_nodes
    custom_search_nodes = []

    for m in re.finditer(r"(ankihub_\w+):(\w*)", ctx.search):
        if m.group(1) == "ankihub_id":
            continue

        parameter_name, parameter_value = m.group(1), m.group(2)
        try:
            custom_search_nodes.append(
                CustomSearchNode.from_parameter_type_and_value(
                    parameter_name, parameter_value
                )
            )
        except ValueError as e:
            showWarning(f"AnkiHub search error: {e}")
            return

        # remove the custom search parameter from the search string
        ctx.search = ctx.search.replace(m.group(0), "")


def on_browser_did_search(ctx: SearchContext):
    # Detach the ankihub database in case it was attached in on_browser_will_search_handle_custom_column_ordering.
    # The attached_ankihub_db context manager can't be used for this because the database query happens
    # in the rust backend.
    detach_ankihub_db_from_anki_db_connection()

    on_browser_did_search_handle_custom_search_parameters(ctx)


def on_browser_did_search_handle_custom_search_parameters(ctx: SearchContext):
    global custom_search_nodes

    if not custom_search_nodes:
        return

    with attached_ankihub_db():
        try:
            for node in custom_search_nodes:
                ctx.ids = node.filter_ids(ctx.ids)
        except ValueError as e:
            showWarning(f"AnkiHub search error: {e}")
            return
        finally:
            custom_search_nodes = []


def on_browser_will_build_tree(
    handled: bool,
    tree: SidebarItem,
    stage: SidebarStage,
    browser: Browser,
):
    if stage != SidebarStage.ROOT:
        return handled

    ankihub_item = tree.add_simple(
        name="👑 AnkiHub",
        icon="AnkiHub",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=SearchNode(
            parsable_text="ankihub_id:*",
        ),
    )

    ankihub_item.add_simple(
        name="With AnkiHub ID",
        icon="With AnkiHub ID",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:_*"),
    )

    ankihub_item.add_simple(
        name="ID Pending",
        icon="ID Pending",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:"),
    )

    ankihub_item.add_simple(
        name="Modified After Sync",
        icon="Modified After Sync",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:yes"
        ),
    )

    ankihub_item.add_simple(
        name="Not Modified After Sync",
        icon="Not Modified After Sync",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:no"
        ),
    )

    updated_today_item = ankihub_item.add_simple(
        name="Updated Today",
        icon="Updated Today",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:_*"),
            SearchNode(
                parsable_text=f"{UpdatedInTheLastXDaysSearchNode.parameter_name}:1"
            ),
        ),
    )

    for suggestion_type in SuggestionType:
        suggestion_value, suggestion_name = suggestion_type.value
        # anki doesn't allow slashes in search parameters
        suggestion_value_escaped = suggestion_value.replace("/", "_slash_")
        updated_today_item.add_simple(
            name=suggestion_name,
            icon=suggestion_name,
            type=SidebarItemType.SAVED_SEARCH,
            search_node=mw.col.group_searches(
                SearchNode(parsable_text="ankihub_id:_*"),
                SearchNode(
                    parsable_text=f"{UpdatedInTheLastXDaysSearchNode.parameter_name}:1"
                ),
                SearchNode(
                    parsable_text=f"{SuggestionTypeSearchNode.parameter_name}:{suggestion_value_escaped}"
                ),
            ),
        )

    updated_today_item = ankihub_item.add_simple(
        name="Updated Since Last Review",
        icon="Updated Since Last Review",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:_*"),
            SearchNode(
                parsable_text=f"{UpdatedSinceLastReviewSearchNode.parameter_name}:"
            ),
        ),
    )

    return handled


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

    browser_will_build_tree.append(on_browser_will_build_tree)
