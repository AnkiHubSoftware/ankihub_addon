import re
import uuid
from concurrent.futures import Future
from pprint import pformat
from typing import List, Optional, Sequence, Tuple

from anki.collection import SearchNode
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
    browser_menus_did_init,
    browser_will_build_tree,
    browser_will_search,
    browser_will_show,
    browser_will_show_context_menu,
)
from aqt.qt import QAction, QMenu, qconnect
from aqt.utils import showInfo, showText, showWarning, tooltip

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError, SuggestionType
from ..db import (
    ankihub_db,
    attach_ankihub_db_to_anki_db_connection,
    attached_ankihub_db,
    detach_ankihub_db_from_anki_db_connection,
)
from ..importing import get_fields_protected_by_tags
from ..note_conversion import TAG_FOR_PROTECTING_ALL_FIELDS, TAG_FOR_PROTECTING_FIELDS
from ..reset_changes import reset_local_changes_to_notes
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, AnkiHubCommands, DeckConfig, config
from ..subdecks import build_subdecks_and_move_cards_to_them
from ..suggestions import BulkNoteSuggestionsResult, suggest_notes_in_bulk
from .custom_columns import (
    AnkiHubIdColumn,
    CustomColumn,
    EditedAfterSyncColumn,
    UpdatedSinceLastReviewColumn,
)
from .custom_search_nodes import (
    CustomSearchNode,
    ModifiedAfterSyncSearchNode,
    SuggestionTypeSearchNode,
    UpdatedInTheLastXDaysSearchNode,
    UpdatedSinceLastReviewSearchNode,
)
from .suggestion_dialog import SuggestionDialog
from .utils import ask_user, choose_list, choose_subset

browser: Optional[Browser] = None
ankihub_tree_item: Optional[SidebarItem] = None

custom_columns = [
    AnkiHubIdColumn(),
    EditedAfterSyncColumn(),
    UpdatedSinceLastReviewColumn(),
]

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
            browser=browser,
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
                    browser, parameter_name, parameter_value
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
    global ankihub_tree_item

    if stage == SidebarStage.ROOT:
        ankihub_tree_item = add_ankihub_tree(tree)
        return handled
    elif stage == SidebarStage.TAGS:
        move_ankihub_tags_to_ankihub_tree(tree, ankihub_tree_item, browser)
        return True
    else:
        return handled


def move_ankihub_tags_to_ankihub_tree(
    root_tree_item: SidebarItem, ankihub_tree_item: SidebarItem, browser: Browser
):
    # build the tag tree using the original function
    browser.sidebar._tag_tree(root_tree_item)

    # move the AnkiHub tags to the AnkiHub tree
    tag_tree = next(
        (item for item in root_tree_item.children if item.name == "Tags"), None
    )

    if tag_tree is None:
        LOGGER.warning("AnkiHub: Could not find tag tree")
        return

    ankihub_tag_tree_items = [
        item for item in tag_tree.children if item.name.startswith("AnkiHub_")
    ]

    for ah_tag_tree_item in ankihub_tag_tree_items:
        tag_tree.children.remove(ah_tag_tree_item)
        ankihub_tree_item.children.append(ah_tag_tree_item)

        ah_tag_tree_item._parent_item = ankihub_tree_item
        ah_tag_tree_item.item_type = SidebarItemType.CUSTOM

        # remove tag icons because it looks better without them
        ah_tag_tree_item.icon = ""
        for descendant in _sidebar_item_descendants(ah_tag_tree_item):
            descendant.icon = ""

    LOGGER.debug("AnkiHub: Moved AnkiHub tag items to AnkiHub tree")


def _sidebar_item_descendants(item: SidebarItem) -> List[SidebarItem]:
    result = []
    for child in item.children:
        result.append(child)
        result.extend(_sidebar_item_descendants(child))
    return result


def add_ankihub_tree(tree: SidebarItem) -> SidebarItem:

    result = tree.add_simple(
        name="👑 AnkiHub",
        icon="AnkiHub",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=SearchNode(
            parsable_text="ankihub_id:*",
        ),
    )
    result.expanded = config.ui_config().ankihub_tree_expanded
    result.on_expanded = set_ah_tree_expanded_in_ui_config

    result.add_simple(
        name="With AnkiHub ID",
        icon="With AnkiHub ID",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:_*"),
    )

    result.add_simple(
        name="ID Pending",
        icon="ID Pending",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:"),
    )

    result.add_simple(
        name="Modified After Sync",
        icon="Modified After Sync",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:yes"
        ),
    )

    result.add_simple(
        name="Not Modified After Sync",
        icon="Not Modified After Sync",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:no"
        ),
    )

    updated_today_item = result.add_simple(
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
    updated_today_item.expanded = config.ui_config().updated_today_tree_expanded
    updated_today_item.on_expanded = set_updated_today_tree_expanded_in_ui_config

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

    result.add_simple(
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
    return result


def set_ah_tree_expanded_in_ui_config(expanded: bool):
    ui_config = config.ui_config()
    ui_config.ankihub_tree_expanded = expanded
    config.set_ui_config(ui_config)


def set_updated_today_tree_expanded_in_ui_config(expanded: bool):
    ui_config = config.ui_config()
    ui_config.updated_today_tree_expanded = expanded
    config.set_ui_config(ui_config)


def store_browser_reference(browser_: Browser) -> None:
    global browser

    browser = browser_


def setup() -> None:
    browser_will_show.append(store_browser_reference)

    browser_did_fetch_columns.append(on_browser_did_fetch_columns)
    browser_did_fetch_row.append(on_browser_did_fetch_row)
    browser_will_search.append(on_browser_will_search)
    browser_did_search.append(on_browser_did_search)

    browser_will_show_context_menu.append(on_browser_will_show_context_menu)

    browser_will_build_tree.append(on_browser_will_build_tree)

    browser_menus_did_init.append(on_browser_menus_did_init)
