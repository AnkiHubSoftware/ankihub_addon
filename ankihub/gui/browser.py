import re
import uuid
from concurrent.futures import Future
from pprint import pformat
from typing import List, Optional, Sequence, Tuple

import aqt
from anki.collection import SearchNode
from anki.notes import NoteId
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
from aqt.utils import showInfo, showText, showWarning, tooltip, tr

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError, SuggestionType
from ..db import (
    ankihub_db,
    attach_ankihub_db_to_anki_db_connection,
    attached_ankihub_db,
    detach_ankihub_db_from_anki_db_connection,
)
from ..importing import get_fields_protected_by_tags
from ..note_conversion import (
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    is_tag_for_group,
)
from ..reset_changes import reset_local_changes_to_notes
from ..settings import (
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    AnkiHubCommands,
    DeckConfig,
    DeckExtensionConfig,
    config,
)
from ..subdecks import SUBDECK_TAG, build_subdecks_and_move_cards_to_them
from ..suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    BulkNoteSuggestionsResult,
    suggest_notes_in_bulk,
)
from ..sync import ah_sync
from .custom_columns import (
    AnkiHubIdColumn,
    CustomColumn,
    EditedAfterSyncColumn,
    UpdatedSinceLastReviewColumn,
)
from .custom_search_nodes import (
    CustomSearchNode,
    ModifiedAfterSyncSearchNode,
    NewNoteSearchNode,
    SuggestionTypeSearchNode,
    UpdatedInTheLastXDaysSearchNode,
    UpdatedSinceLastReviewSearchNode,
)
from .optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
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


# context menu
def _on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    selected_nids = browser.selected_notes()
    selected_nid = None
    ankihub_nid = None
    if len(selected_nids) == 1:
        selected_nid = selected_nids[0]
        ankihub_nid = ankihub_db.ankihub_nid_for_anki_nid(selected_nid)

    menu = context_menu

    menu.addSeparator()

    menu.addAction(
        "AnkiHub: Bulk suggest notes",
        lambda: _on_bulk_notes_suggest_action(browser, nids=selected_nids),
    )

    protect_fields_action = menu.addAction(
        "AnkiHub: Protect fields",
        lambda: _on_protect_fields_action(browser, nid=selected_nid),
    )
    if len(selected_nids) != 1:
        protect_fields_action.setDisabled(True)

    menu.addAction(
        "AnkiHub: Reset local changes",
        lambda: _on_reset_local_changes_action(browser, nids=selected_nids),
    )

    menu.addAction(
        "AnkiHub: Suggest Optional Tags",
        lambda: _on_suggest_optional_tags_action(browser),
    )

    copy_ankihub_id_action = menu.addAction(
        "AnkiHub: Copy AnkiHub ID to clipboard",
        lambda: aqt.mw.app.clipboard().setText(str(ankihub_nid)),
    )
    if len(selected_nids) != 1 or not ankihub_nid:
        copy_ankihub_id_action.setDisabled(True)


def _on_protect_fields_action(browser: Browser, nid: NoteId) -> None:
    note = aqt.mw.col.get_note(nid)
    if not ankihub_db.is_ankihub_note_type(note.mid):
        showInfo(
            "This note does not have a note type that is known by AnkiHub.",
            parent=browser,
        )
        return

    note = aqt.mw.col.get_note(nid)

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

    LOGGER.info(
        f"Updated tags for protecting fields for note {note.id} to protect these fields {new_fields_protected_by_tags}"
    )


def _on_bulk_notes_suggest_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    notes = [aqt.mw.col.get_note(nid) for nid in nids]

    mids = set(note.mid for note in notes)
    if not all(ankihub_db.is_ankihub_note_type(mid) for mid in mids):
        showInfo(
            "Some of the notes you selected are not of a note type that is known by AnkiHub."
        )
        return

    if len(notes) > 500:
        msg = "Please select less than 500 notes at a time for bulk suggestions.<br>"
        showInfo(msg, parent=browser)
        return

    if not (dialog := SuggestionDialog(command=AnkiHubCommands.CHANGE)).exec():
        return

    aqt.mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            notes,
            auto_accept=dialog.auto_accept(),
            change_type=dialog.change_type(),
            comment=dialog.comment(),
        ),
        on_done=lambda future: _on_suggest_notes_in_bulk_done(future, browser),
        parent=browser,
    )


def _on_suggest_notes_in_bulk_done(future: Future, browser: Browser) -> None:
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

    LOGGER.info("Created note suggestions in bulk.")
    LOGGER.info(f"errors_by_nid:\n{pformat(suggestions_result.errors_by_nid)}")

    msg_about_created_suggestions = (
        f"Submitted {suggestions_result.change_note_suggestions_count} change note suggestion(s).\n"
        f"Submitted {suggestions_result.new_note_suggestions_count} new note suggestion(s) to.\n\n\n"
    )

    notes_without_changes = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if ANKIHUB_NO_CHANGE_ERROR in str(errors)
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


def _on_reset_local_changes_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    if not ankihub_db.are_ankihub_notes(list(nids)):
        showInfo(
            "Please only select notes with AnkiHub ids to reset local changes.",
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

    aqt.mw.taskman.with_progress(
        task=lambda: reset_local_changes_to_notes(nids, ankihub_deck_uuid=ankihub_did),
        on_done=on_done,
        label="Resetting local changes...",
        parent=browser,
    )


def _on_suggest_optional_tags_action(browser: Browser) -> None:
    nids = browser.selected_notes()

    if not nids:
        return

    if not ankihub_db.are_ankihub_notes(list(nids)):
        showInfo(
            "Please only select notes from an AnkiHub deck to suggest optional tags.",
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

    OptionalTagsSuggestionDialog(parent=browser, nids=nids).exec()


# AnkiHub menu
def _on_browser_menus_did_init(browser: Browser):
    menu = browser._ankihub_menu = QMenu("AnkiHub")  # type: ignore
    browser.form.menubar.addMenu(menu)

    reset_deck_action = QAction("Reset all local changes to a deck", browser)
    qconnect(reset_deck_action.triggered, lambda: _on_reset_deck_action(browser))
    menu.addAction(reset_deck_action)

    reset_subdecks_action = QAction(
        "Rebuild subdecks and move cards into subdecks", browser
    )
    qconnect(
        reset_subdecks_action.triggered, lambda: _on_reset_subdecks_action(browser)
    )
    menu.addAction(reset_subdecks_action)

    reset_optional_tags_action = QAction("Reset an Optional Tag Group", browser)
    qconnect(
        reset_optional_tags_action.triggered,
        lambda: _on_reset_optional_tags_action(browser),
    )
    menu.addAction(reset_optional_tags_action)


def _on_reset_deck_action(browser: Browser):
    if not config.deck_ids():
        showInfo(
            "You don't have any AnkiHub decks configured yet.",
            parent=browser,
        )
        return

    ah_did, deck_config = _choose_deck(
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

    aqt.mw.taskman.with_progress(
        lambda: reset_local_changes_to_notes(nids, ankihub_deck_uuid=ah_did),
        on_done=on_done,
        label="Resetting local changes...",
        parent=browser,
    )


def _on_reset_subdecks_action(browser: Browser):
    if not config.deck_ids():
        showInfo(
            "You don't have any AnkiHub decks configured yet.",
            parent=browser,
        )
        return

    ah_did, deck_config = _choose_deck(
        "Choose the AnkiHub deck for which<br>"
        "you want to rebuild subdecks and move<br>"
        "cards to their original subdeck.<br><br>"
        "<b>Note:</b> This will only move<br>"
        "cards of notes that have subdeck tags<br>"
        f"(tags starting with <b>{SUBDECK_TAG})</b>.",
    )
    if ah_did is None:
        return

    if aqt.mw.col.decks.name_if_exists(deck_config.anki_id) is None:
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
        aqt.mw.deckBrowser.refresh()
        tooltip("Rebuilt subdecks and moved cards.")

    aqt.mw.taskman.with_progress(
        task=lambda: build_subdecks_and_move_cards_to_them(ankihub_did=ah_did),
        on_done=on_done,
        label="Rebuilding subdecks and moving cards...",
    )


def _on_reset_optional_tags_action(browser: Browser):
    if not (extension_ids := config.deck_extension_ids()):
        showInfo(
            "You don't have any AnkiHub optional tag groups configured yet.",
            parent=browser,
        )
        return

    if not ah_sync.is_logged_in():
        showInfo(
            "You need to be logged in to AnkiHub to reset optional tag groups.",
            parent=browser,
        )
        return

    extension_configs = [config.deck_extension_config(eid) for eid in extension_ids]
    tag_group_names = [c.tag_group_name for c in extension_configs]
    deck_configs = [config.deck_config(c.ankihub_deck_uuid) for c in extension_configs]
    tag_group_names_with_deck = [
        f"{extension_name} ({deck_config.name})"
        for extension_name, deck_config in zip(tag_group_names, deck_configs)
    ]

    extension_idx = choose_list(
        "Choose the optional tag group which<br>" "you want to reset.",
        choices=tag_group_names_with_deck,
    )
    if extension_idx is None:
        return

    extension_id = extension_ids[extension_idx]
    tag_group_name_with_deck = tag_group_names_with_deck[extension_idx]

    if not ask_user(
        "Are you sure you want to reset the optional tag group "
        f"<b>{tag_group_name_with_deck}</b>?<br><br>"
        "Note: This will sync all AnkiHub decks.",
        parent=browser,
        defaultno=True,
    ):
        return

    def on_done(future: Future) -> None:
        future.result()

        tooltip(
            f"Reset optional tag group {tag_group_name_with_deck} successfully.",
            parent=browser,
        )

    aqt.mw.taskman.with_progress(
        task=lambda: _reset_optional_tag_group(extension_id=extension_id),
        on_done=on_done,
        label=f"Removing optional tags for {tag_group_name_with_deck}...",
    )


def _reset_optional_tag_group(extension_id: int) -> None:

    extension_config = config.deck_extension_config(extension_id)
    _remove_optional_tags_of_extension(extension_config)

    # reset the latest extension update to sync all content for the deck extension on the next sync
    config.save_latest_extension_update(extension_id=extension_id, latest_update=None)

    # sync with ankihub to re-download the deck extension
    # TODO only sync the deck extension or the related deck instead of all decks
    ah_sync.sync_all_decks()


def _remove_optional_tags_of_extension(extension_config: DeckExtensionConfig) -> None:
    # only removes the tags for notes of the deck related to the deck extension,
    # some people use the same tag group for multiple decks
    tags_for_tag_group = [
        tag
        for tag in aqt.mw.col.tags.all()
        if is_tag_for_group(tag, extension_config.tag_group_name)
    ]
    nids = ankihub_db.anki_nids_for_ankihub_deck(extension_config.ankihub_deck_uuid)
    aqt.mw.col.tags.bulk_remove(note_ids=nids, tags=" ".join(tags_for_tag_group))
    LOGGER.info(f"Removed optional tags for {extension_config.tag_group_name}")


def _choose_deck(prompt: str) -> Tuple[Optional[uuid.UUID], Optional[DeckConfig]]:
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


# custom columns
def _on_browser_did_fetch_columns(columns: dict[str, Column]):
    for column in custom_columns:
        columns[column.key] = column.builtin_column


def _on_browser_did_fetch_row(
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


# cutom search nodes
def _on_browser_will_search(ctx: SearchContext):
    _on_browser_will_search_handle_custom_column_ordering(ctx)
    _on_browser_will_search_handle_custom_search_parameters(ctx)


def _on_browser_will_search_handle_custom_column_ordering(ctx: SearchContext):
    if not isinstance(ctx.order, Column):
        return

    custom_column: CustomColumn = next(
        (c for c in custom_columns if c.builtin_column.key == ctx.order.key), None
    )
    if custom_column is None:
        return

    attach_ankihub_db_to_anki_db_connection()

    ctx.order = custom_column.order_by_str()


def _on_browser_will_search_handle_custom_search_parameters(ctx: SearchContext):
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


def _on_browser_did_search(ctx: SearchContext):
    # Detach the ankihub database in case it was attached in on_browser_will_search_handle_custom_column_ordering.
    # The attached_ankihub_db context manager can't be used for this because the database query happens
    # in the rust backend.
    detach_ankihub_db_from_anki_db_connection()

    _on_browser_did_search_handle_custom_search_parameters(ctx)


def _on_browser_did_search_handle_custom_search_parameters(ctx: SearchContext):
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


# sidebar
def _on_browser_will_build_tree(
    handled: bool,
    tree: SidebarItem,
    stage: SidebarStage,
    browser: Browser,
):
    global ankihub_tree_item

    if stage == SidebarStage.ROOT:
        ankihub_tree_item = _add_ankihub_tree(tree)
        return handled
    elif stage == SidebarStage.TAGS:
        if _build_tag_tree_and_copy_ah_tag_items_to_ah_tree(
            tree, ankihub_tree_item, browser
        ):
            return True
        else:
            return handled
    else:
        return handled


def _build_tag_tree_and_copy_ah_tag_items_to_ah_tree(
    root_tree_item: SidebarItem, ankihub_tree_item: SidebarItem, browser: Browser
) -> bool:
    """Build the tag tree and copy AnkiHub tag items to the AnkiHub tree so
    that all AnkiHub related sidebar items are grouped together.
    The tag items should still be in the tag tree to avoid confusion and to
    allow users to use the context menu actions on them.
    Returns True if the tag tree was built successfully, False otherwise.
    Building the tag tree can fail if related Anki functions change in the future.
    """

    # build the tag tree using the original function used by Anki
    try:
        browser.sidebar._tag_tree(root_tree_item)
    except (AttributeError, ValueError):
        LOGGER.warning("AnkiHub: Could not build tag tree")
        return False

    # move the AnkiHub tag items to the AnkiHub tree
    tag_tree = next(
        (
            item
            for item in root_tree_item.children
            if item.name == tr.browsing_sidebar_tags()
        ),
        None,
    )

    if tag_tree is None:
        LOGGER.warning("AnkiHub: Could not find tag tree")
        return False

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

    # remove and re-add the tag tree so that the AnkiHub tag items are under the AnkiHub tree
    # and also under the tag tree
    root_tree_item.children.remove(tag_tree)
    browser.sidebar._tag_tree(root_tree_item)

    LOGGER.info("AnkiHub: Built tag tree and copied AnkiHub tag items to AnkiHub tree")

    return True


def _sidebar_item_descendants(item: SidebarItem) -> List[SidebarItem]:
    result = []
    for child in item.children:
        result.append(child)
        result.extend(_sidebar_item_descendants(child))
    return result


def _add_ankihub_tree(tree: SidebarItem) -> SidebarItem:

    result = tree.add_simple(
        name="👑 AnkiHub",
        icon="",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=SearchNode(
            parsable_text="ankihub_id:*",
        ),
    )
    result.expanded = config.ui_config().ankihub_tree_expanded
    result.on_expanded = _set_ah_tree_expanded_in_ui_config

    result.add_simple(
        name="With AnkiHub ID",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:_*"),
    )

    result.add_simple(
        name="ID Pending",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(parsable_text="ankihub_id:"),
    )

    result.add_simple(
        name="Modified After Sync",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:yes"
        ),
    )

    result.add_simple(
        name="Not Modified After Sync",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=SearchNode(
            parsable_text=f"{ModifiedAfterSyncSearchNode.parameter_name}:no"
        ),
    )

    updated_today_item = result.add_simple(
        name="Updated Today",
        icon="",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=aqt.mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:_*"),
            SearchNode(
                parsable_text=f"{UpdatedInTheLastXDaysSearchNode.parameter_name}:1"
            ),
        ),
    )
    updated_today_item.expanded = config.ui_config().updated_today_tree_expanded
    updated_today_item.on_expanded = _set_updated_today_tree_expanded_in_ui_config

    updated_today_item.add_simple(
        name="New Note",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=aqt.mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:_*"),
            SearchNode(
                parsable_text=f"{UpdatedInTheLastXDaysSearchNode.parameter_name}:1"
            ),
            SearchNode(parsable_text=f"{NewNoteSearchNode.parameter_name}:"),
        ),
    )

    for suggestion_type in SuggestionType:
        suggestion_value, suggestion_name = suggestion_type.value
        # anki doesn't allow slashes in search parameters
        suggestion_value_escaped = suggestion_value.replace("/", "_slash_")
        updated_today_item.add_simple(
            name=suggestion_name,
            icon="",
            type=SidebarItemType.SAVED_SEARCH,
            search_node=aqt.mw.col.group_searches(
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
        icon="",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=aqt.mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:_*"),
            SearchNode(
                parsable_text=f"{UpdatedSinceLastReviewSearchNode.parameter_name}:"
            ),
        ),
    )
    return result


def _set_ah_tree_expanded_in_ui_config(expanded: bool):
    ui_config = config.ui_config()
    ui_config.ankihub_tree_expanded = expanded
    config.set_ui_config(ui_config)


def _set_updated_today_tree_expanded_in_ui_config(expanded: bool):
    ui_config = config.ui_config()
    ui_config.updated_today_tree_expanded = expanded
    config.set_ui_config(ui_config)


# setup
def _store_browser_reference(browser_: Browser) -> None:
    global browser

    browser = browser_


def setup() -> None:
    browser_will_show.append(_store_browser_reference)

    browser_did_fetch_columns.append(_on_browser_did_fetch_columns)
    browser_did_fetch_row.append(_on_browser_did_fetch_row)
    browser_will_search.append(_on_browser_will_search)
    browser_did_search.append(_on_browser_did_search)

    browser_will_show_context_menu.append(_on_browser_will_show_context_menu)

    browser_will_build_tree.append(_on_browser_will_build_tree)

    browser_menus_did_init.append(_on_browser_menus_did_init)
