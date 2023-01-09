import uuid
from abc import abstractmethod
from concurrent.futures import Future
from pprint import pformat
from typing import List, Optional, Sequence, Tuple

from anki.collection import BrowserColumns
from anki.notes import Note
from anki.utils import ids2str
from aqt import mw
from aqt.browser import Browser, CellRow, Column, ItemId, SearchContext
from aqt.gui_hooks import (
    browser_did_fetch_columns,
    browser_did_fetch_row,
    browser_did_search,
    browser_menus_did_init,
    browser_will_search,
    browser_will_show,
    browser_will_show_context_menu,
)
from aqt.qt import QAction, QMenu, qconnect
from aqt.utils import showInfo, showText, tooltip

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError
from ..db import (
    ankihub_db,
    attach_ankihub_db_to_anki_db_connection,
    detach_ankihub_db_from_anki_db_connection,
)
from ..importing import get_fields_protected_by_tags
from ..note_conversion import TAG_FOR_PROTECTING_ALL_FIELDS, TAG_FOR_PROTECTING_FIELDS
from ..reset_changes import reset_local_changes_to_notes
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, AnkiHubCommands, DeckConfig, config
from ..subdecks import build_subdecks_and_move_cards_to_them
from ..suggestions import BulkNoteSuggestionsResult, suggest_notes_in_bulk
from ..utils import note_types_with_ankihub_id_field
from .suggestion_dialog import SuggestionDialog
from .utils import ask_user, choose_list, choose_subset

browser: Optional[Browser] = None


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

    menu.addAction(
        "AnkiHub: Reset local changes",
        lambda: on_reset_local_changes_action(browser),
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


def on_reset_local_changes_action(browser: Browser) -> None:
    nids = browser.selected_notes()

    if not nids:
        return

    if not ankihub_db.are_ankihub_notes(list(nids)):
        showInfo(
            "Please only select notes from an AnkiHub deck to reset local changes.",
            parent=browser,
        )
        return

    ankihub_dids = ankihub_db.ankihub_dids_for_anki_nids(nids)

    if len(ankihub_dids) > 1:
        showInfo(
            "Please select notes from only one AnkiHub deck at a time.",
            parent=browser,
        )
        return

    ankihub_did = list(ankihub_dids)[0]

    def on_done(future: Future) -> None:
        future.result()  # raise exception if there was one

        browser.table.reset()
        tooltip("Reset local changes for selected notes.", parent=browser)

    mw.taskman.with_progress(
        task=lambda: reset_local_changes_to_notes(nids, ankihub_deck_uuid=ankihub_did),
        on_done=on_done,
        label="Resetting local changes...",
        parent=browser,
    )


def on_browser_menus_did_init(browser: Browser):
    menu = browser._ankihub_menu = QMenu("AnkiHub")  # type: ignore
    browser.form.menubar.addMenu(menu)

    reset_deck_action = QAction("Reset all local changes to a deck", browser)
    qconnect(reset_deck_action.triggered, lambda: on_reset_deck_action(browser))
    menu.addAction(reset_deck_action)

    reset_subdecks_action = QAction(
        "Rebuild subdecks and move cards into subdecks", browser
    )
    qconnect(reset_subdecks_action.triggered, lambda: on_reset_subdecks_action(browser))
    menu.addAction(reset_subdecks_action)


def on_reset_deck_action(browser: Browser):
    if not config.deck_ids():
        showInfo(
            "You don't have any AnkiHub decks configured yet.",
            parent=browser,
        )
        return

    ah_did, deck_config = choose_deck(
        "Choose the AnkiHub deck for which<br>you want to reset local changes"
    )
    if ah_did is None:
        return

    if not ask_user(
        f"Are you sure you want to reset all local changes to the deck <b>{deck_config.name}</b>?",
        parent=browser,
    ):
        return

    nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)

    def on_done(future: Future) -> None:
        future.result()

        browser.model.reset()
        tooltip(f"Reset local changes to deck <b>{deck_config.name}</b>")

    mw.taskman.with_progress(
        lambda: reset_local_changes_to_notes(nids, ankihub_deck_uuid=ah_did),
        on_done=on_done,
        label="Resetting local changes...",
        parent=browser,
    )


def on_reset_subdecks_action(browser: Browser):
    if not config.deck_ids():
        showInfo(
            "You don't have any AnkiHub decks configured yet.",
            parent=browser,
        )
        return

    ah_did, deck_config = choose_deck(
        "Choose the AnkiHub deck for which<br>"
        "you want to rebuild subdecks and move<br>"
        "cards to their original subdeck."
    )
    if ah_did is None:
        return

    if mw.col.decks.name_if_exists(deck_config.anki_id) is None:
        showInfo(
            (
                f"Anki deck <b>{deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                "It might help to reset local changes to the deck first.<br>"
                "(You can do that from the AnkiHub menu in the Anki browser.)"
            ),
            parent=browser,
        )
        return

    if not ask_user(
        f"Are you sure you want to rebuild subdecks for <b>{deck_config.name}</b> "
        "and move cards to their original subdecks?",
        parent=browser,
    ):
        return

    def on_done(future: Future) -> None:
        future.result()
        browser.sidebar.refresh()
        mw.deckBrowser.refresh()
        tooltip("Rebuilt subdecks and moved cards.")

    mw.taskman.with_progress(
        task=lambda: build_subdecks_and_move_cards_to_them(ankihub_did=ah_did),
        on_done=on_done,
        label="Rebuilding subdecks and moving cards...",
    )


def choose_deck(prompt: str) -> Tuple[Optional[uuid.UUID], Optional[DeckConfig]]:
    ah_dids = config.deck_ids()
    deck_configs = [config.deck_config(did) for did in ah_dids]
    chosen_deck_idx = choose_list(
        prompt=prompt,
        choices=[deck.name for deck in deck_configs],
        parent=browser,
    )

    if chosen_deck_idx is None:
        return None, None

    chosen_deck_ah_did = ah_dids[chosen_deck_idx]
    chosen_deck_config = deck_configs[chosen_deck_idx]
    return chosen_deck_ah_did, chosen_deck_config


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

    attach_ankihub_db_to_anki_db_connection()

    ctx.order = custom_column.order_by_str()


def on_browser_did_search(ctx: SearchContext):
    detach_ankihub_db_from_anki_db_connection()


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

    browser_menus_did_init.append(on_browser_menus_did_init)
