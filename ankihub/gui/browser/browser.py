"""Modifies the Anki browser (aqt.browser) to add AnkiHub features."""

import re
from concurrent.futures import Future
from typing import List, Optional, Sequence

import aqt
from anki.collection import SearchNode
from anki.hooks import wrap
from anki.notes import NoteId
from aqt.addcards import AddCards
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
from aqt.utils import showInfo, showWarning, tooltip, tr

from ... import LOGGER
from ...ankihub_client import SuggestionType
from ...db import ankihub_db
from ...main.importing import get_fields_protected_by_tags
from ...main.note_conversion import (
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    optional_tag_prefix_for_group,
)
from ...main.reset_local_changes import reset_local_changes_to_notes
from ...main.subdecks import SUBDECK_TAG, build_subdecks_and_move_cards_to_them
from ...main.utils import mids_of_notes, retain_nids_with_ah_note_type
from ...settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, DeckExtensionConfig, config
from ..deck_updater import NotLoggedInError
from ..operations.ankihub_sync import update_decks_and_media
from ..optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
from ..suggestion_dialog import (
    open_suggestion_dialog_for_bulk_suggestion,
    open_suggestion_dialog_for_single_suggestion,
)
from ..utils import ask_user, choose_ankihub_deck, choose_list, choose_subset
from .custom_columns import (
    AnkiHubIdColumn,
    EditedAfterSyncColumn,
    UpdatedSinceLastReviewColumn,
)
from .custom_search_nodes import (
    AnkiHubNoteSearchNode,
    CustomSearchNode,
    ModifiedAfterSyncSearchNode,
    NewNoteSearchNode,
    SuggestionTypeSearchNode,
    UpdatedInTheLastXDaysSearchNode,
    UpdatedSinceLastReviewSearchNode,
)

# Maximum number of notes that can be selected for bulk suggestions.
BULK_SUGGESTION_LIMIT = 2000

# Various special tags used by AnkiHub have this prefix. The sidebar items of tags with this prefix
# are copied to the AnkiHub tree in the sidebar.
ANKIHUB_TAGS_PREFIX = "ankihub_"

# These tags are not copied to the AnkiHub tree in the sidebar.
ANKIHUB_TAGS_EXCLUDED_FROM_TAG_TREE = ["ankihub_deleted"]

browser: Optional[Browser] = None
ankihub_tree_item: Optional[SidebarItem] = None

custom_columns = [
    AnkiHubIdColumn(),
    EditedAfterSyncColumn(),
    UpdatedSinceLastReviewColumn(),
]

# stores the custom search nodes for the current search
custom_search_nodes: List[CustomSearchNode] = []


def setup() -> None:
    browser_will_show.append(_store_browser_reference)

    _setup_custom_columns()
    _setup_search()
    _setup_context_menu()
    _setup_ankihub_sidebar_tree()
    _setup_ankihub_menu()
    _make_copy_note_action_not_copy_ankihub_id()


def _store_browser_reference(browser_: Browser) -> None:
    global browser

    browser = browser_


# context menu
def _setup_context_menu():
    browser_will_show_context_menu.append(_on_browser_will_show_context_menu)


def _on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    """Adds AnkiHub menu actions to the browser context menu."""

    context_menu.addSeparator()

    selected_nids = browser.selected_notes()

    mids = mids_of_notes(selected_nids)
    at_least_one_note_has_ah_note_type = any(
        ankihub_db.is_ankihub_note_type(mid) for mid in mids
    )

    exactly_one_note_selected = len(selected_nids) == 1

    exactly_one_ah_note_selected = len(selected_nids) == 1 and bool(
        ankihub_db.ankihub_nid_for_anki_nid(selected_nids[0])
    )

    # List of (name, function, enabled) tuples for the actions to add to the context menu.
    actions = [
        (
            "AnkiHub: Bulk suggest notes",
            lambda: _on_bulk_notes_suggest_action(browser, nids=selected_nids),
            at_least_one_note_has_ah_note_type,
        ),
        (
            "AnkiHub: Suggest to delete note",
            lambda: _on_bulk_notes_suggest_action(
                browser,
                nids=selected_nids,
                preselected_change_type=SuggestionType.DELETE,
            ),
            at_least_one_note_has_ah_note_type,
        ),
        (
            "AnkiHub: Protect fields",
            lambda: _on_protect_fields_action(browser, nids=selected_nids),
            at_least_one_note_has_ah_note_type,
        ),
        (
            "AnkiHub: Reset local changes",
            lambda: _on_reset_local_changes_action(browser, nids=selected_nids),
            at_least_one_note_has_ah_note_type,
        ),
        (
            "AnkiHub: Suggest Optional Tags",
            lambda: _on_suggest_optional_tags_action(browser),
            at_least_one_note_has_ah_note_type,
        ),
        (
            "AnkiHub: Copy Anki Note ID to clipboard",
            lambda: _on_copy_anki_nid_action(browser, selected_nids),
            exactly_one_note_selected,
        ),
        (
            "AnkiHub: Copy AnkiHub Note ID to clipboard",
            lambda: _on_copy_ankihub_nid_action(browser, selected_nids),
            exactly_one_ah_note_selected,
        ),
    ]

    for name, func, enabled in actions:
        action = context_menu.addAction(name, func)
        action.setEnabled(enabled)


def _on_copy_anki_nid_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    anki_nid = nids[0]
    aqt.mw.app.clipboard().setText(str(anki_nid))


def _on_copy_ankihub_nid_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    nid = nids[0]
    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(nid)
    aqt.mw.app.clipboard().setText(str(ah_nid))


def _on_protect_fields_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    mids = mids_of_notes(nids)
    if len(mids) != 1:
        showInfo(
            "Please select notes of only one note type.",
            parent=browser,
        )
        return

    note = aqt.mw.col.get_note(nids[0])
    field_names: List[str] = [
        field_name
        for field_name in note.keys()
        if field_name != ANKIHUB_NOTE_TYPE_FIELD_NAME
    ]
    if len(nids) == 1:
        old_fields_protected_by_tags: List[str] = get_fields_protected_by_tags(note)
    else:
        old_fields_protected_by_tags = []

    new_fields_protected_by_tags = choose_subset(
        "Choose which fields of this note should be protected<br>"
        "from updates.<br><br>"
        "Tip: If you want to protect a field on every note, <br>"
        "consider using the "
        "<a href='https://community.ankihub.net/t/protecting-fields-and-tags'>protected fields feature</a>.",
        choices=field_names,
        current=old_fields_protected_by_tags,
        description_html="This will edit the AnkiHub_Protect tags of the note.",
        parent=browser,
    )
    if new_fields_protected_by_tags is None:
        return

    if set(new_fields_protected_by_tags) == set(field_names):
        # if all fields are protected, we can just use the tag for protecting all fields
        new_tags_for_protecting_fields = [TAG_FOR_PROTECTING_ALL_FIELDS]
    else:
        # otherwise we need to create a tag for each field.
        # spaces are not allowed in tags, so we replace them with underscores
        new_tags_for_protecting_fields = [
            f"{TAG_FOR_PROTECTING_FIELDS}::{field.replace(' ', '_')}"
            for field in new_fields_protected_by_tags
        ]

    def update_note_tags() -> None:
        notes = [aqt.mw.col.get_note(nid) for nid in nids]
        for note in notes:
            # remove old tags for protecting fields
            note.tags = [
                tag
                for tag in note.tags
                if not tag.lower().startswith(TAG_FOR_PROTECTING_FIELDS.lower())
            ]

            # add new tags for protecting fields
            note.tags += new_tags_for_protecting_fields

        # update the notes and add an undo entry
        undo_entry_id = aqt.mw.col.add_custom_undo_entry(
            "Protect fields of note(s) using tags"
        )
        aqt.mw.col.update_notes(notes)
        aqt.mw.col.merge_undo_entries(undo_entry_id)

    def on_done(future: Future) -> None:
        future.result()

        aqt.mw.update_undo_actions()

        # without this the tags in the browser editor are not updated until you switch away from the note
        browser.table.reset()
        tooltip("Updated tags for protecting fields")

        LOGGER.info(
            f"Updated tags for protecting fields for notes\n"
            f"\t{nids=}\n"
            f"\t{new_fields_protected_by_tags=}\n"
        )

    aqt.mw.taskman.with_progress(
        task=update_note_tags,
        on_done=on_done,
        label="Updating tags for protecting fields",
    )


def _on_bulk_notes_suggest_action(
    browser: Browser,
    nids: Sequence[NoteId],
    preselected_change_type: Optional[SuggestionType] = None,
) -> None:
    if len(nids) > BULK_SUGGESTION_LIMIT:
        msg = f"Please select at most {BULK_SUGGESTION_LIMIT} notes at a time for bulk suggestions.<br>"
        showInfo(msg, parent=browser)
        return

    filtered_nids = retain_nids_with_ah_note_type(nids)
    if not filtered_nids:
        showInfo(
            "The selected notes need to have an AnkiHub note type.<br><br>"
            "You can use <b>AnkiHub -> With AnkiHub ID</b> (for suggesting changes to notes) "
            "or <b>AnkiHub -> ID Pending</b> (for suggesting new notes) in the left sidebar to find notes to suggest.",
            parent=browser,
        )
        return

    if len(filtered_nids) != len(nids):
        showInfo(
            f"{len(nids) - len(filtered_nids)} of the {len(nids)} selected notes don't have an AnkiHub note type "
            "and will be ignored.<br><br>"
            "You can use <b>AnkiHub -> With AnkiHub ID</b> (for suggesting changes to notes) "
            "or <b>AnkiHub -> ID Pending</b> (for suggesting new notes) in the left sidebar to find notes to suggest.",
            parent=browser,
        )

    ah_dids = ankihub_db.ankihub_dids_for_anki_nids(filtered_nids)
    if len(ah_dids) > 1:
        msg = (
            "You can only create suggestions for notes from one AnkiHub deck at a time.<br>"
            "Please select notes from only one AnkiHub deck."
        )
        showInfo(msg, parent=browser)
        return

    if len(filtered_nids) == 1:
        nid = list(filtered_nids)[0]
        open_suggestion_dialog_for_single_suggestion(
            note=aqt.mw.col.get_note(nid),
            preselected_change_type=preselected_change_type,
            parent=browser,
        )
    else:
        open_suggestion_dialog_for_bulk_suggestion(
            anki_nids=filtered_nids,
            preselected_change_type=preselected_change_type,
            parent=browser,
        )


def _on_reset_local_changes_action(browser: Browser, nids: Sequence[NoteId]) -> None:
    anki_nid_to_ah_did = ankihub_db.anki_nid_to_ah_did_dict(anki_nids=nids)
    ankihub_dids = set(anki_nid_to_ah_did.values())

    if len(ankihub_dids) == 0:
        showInfo(
            "Please select notes from an AnkiHub deck to reset local changes.",
            parent=browser,
        )
        return

    if len(ankihub_dids) > 1:
        showInfo(
            "Please select notes from only one AnkiHub deck at a time.",
            parent=browser,
        )
        return

    ankihub_did = list(ankihub_dids)[0]
    filtered_nids = list(anki_nid_to_ah_did.keys())

    def on_done(future: Future) -> None:
        future.result()  # raise exception if there was one

        browser.table.reset()
        tooltip("Reset local changes for selected notes.", parent=browser)

    aqt.mw.taskman.with_progress(
        task=lambda: reset_local_changes_to_notes(filtered_nids, ah_did=ankihub_did),
        on_done=on_done,
        label="Resetting local changes...",
        parent=browser,
    )


def _on_suggest_optional_tags_action(browser: Browser) -> None:
    nids = browser.selected_notes()

    if not nids:
        return

    anki_nid_to_ah_did = ankihub_db.anki_nid_to_ah_did_dict(anki_nids=nids)
    ankihub_dids = set(anki_nid_to_ah_did.values())

    if len(ankihub_dids) == 0:
        print("Please select notes from an AnkiHub deck to suggest optional tags.")
        return

    if len(ankihub_dids) > 1:
        showInfo(
            "Please select notes from only one AnkiHub deck at a time.",
            parent=browser,
        )
        return

    filtered_nids = list(anki_nid_to_ah_did.keys())
    OptionalTagsSuggestionDialog(parent=browser, nids=filtered_nids).exec()


# AnkiHub menu
def _setup_ankihub_menu():
    browser_menus_did_init.append(_on_browser_menus_did_init)


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

    ah_did = choose_ankihub_deck(
        "Choose the AnkiHub deck for which<br>you want to reset local changes",
        parent=browser,
    )
    if ah_did is None:
        return

    deck_config = config.deck_config(ah_did)

    if not ask_user(
        f"Are you sure you want to reset all local changes to the deck <b>{deck_config.name}</b>?",
        parent=browser,
    ):
        return

    nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)

    def on_done(future: Future) -> None:
        future.result()
        aqt.mw.reset()
        tooltip(f"Reset local changes to deck <b>{deck_config.name}</b>")

    aqt.mw.taskman.with_progress(
        lambda: reset_local_changes_to_notes(nids, ah_did=ah_did),
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

    ah_did = choose_ankihub_deck(
        "Choose the AnkiHub deck for which<br>"
        "you want to rebuild subdecks and move<br>"
        "cards to their original subdeck.<br><br>"
        "<b>Note:</b> This will only move<br>"
        "cards of notes that have subdeck tags<br>"
        f"(tags starting with <b>{SUBDECK_TAG})</b>.",
        parent=browser,
    )
    if ah_did is None:
        return

    deck_config = config.deck_config(ah_did)

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
        aqt.mw.reset()
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

    if not config.is_logged_in():
        raise NotLoggedInError()

    extension_configs = [config.deck_extension_config(eid) for eid in extension_ids]
    tag_group_names = [c.tag_group_name for c in extension_configs]
    deck_configs = [config.deck_config(c.ah_did) for c in extension_configs]
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
        default_no=True,
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

    def on_done(future: Future) -> None:
        future.result()

    update_decks_and_media(
        on_done=on_done, ah_dids=[extension_config.ah_did], start_media_sync=False
    )


def _remove_optional_tags_of_extension(extension_config: DeckExtensionConfig) -> None:
    """Remove all optional tags of the given extension from all notes of the deck the extension is for."""
    # Some people use the same tag group name for multiple decks, so we have to be careful to
    # only remove tags from notes of the deck the deck extension is for.
    nids = ankihub_db.anki_nids_for_ankihub_deck(extension_config.ah_did)
    aqt.mw.col.tags.find_and_replace(
        note_ids=nids,
        search=f"{optional_tag_prefix_for_group(extension_config.tag_group_name)}.+",
        replacement="",
        regex=True,
        match_case=False,
    )
    LOGGER.info(
        "Removed optional tags for optional tag group.",
        tag_group_name=extension_config.tag_group_name,
    )


# custom columns
def _setup_custom_columns():
    browser_did_fetch_columns.append(_on_browser_did_fetch_columns)
    browser_did_fetch_row.append(_on_browser_did_fetch_row)


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
def _setup_search():
    browser_will_search.append(_on_browser_will_search)
    browser_did_search.append(_on_browser_did_search_handle_custom_search_parameters)


def _on_browser_will_search(ctx: SearchContext):
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


def _on_browser_did_search_handle_custom_search_parameters(ctx: SearchContext):
    global custom_search_nodes

    if not custom_search_nodes:
        return

    try:
        for node in custom_search_nodes:
            ctx.ids = node.filter_ids(ctx.ids)
    except ValueError as e:
        showWarning(f"AnkiHub search error: {e}")
        return
    finally:
        custom_search_nodes = []


# sidebar
def _setup_ankihub_sidebar_tree():
    browser_will_build_tree.append(_on_browser_will_build_tree)


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
    that AnkiHub related sidebar items are grouped together.
    The tag items should still be in the tag tree to avoid confusion and to
    allow users to use the context menu actions on them.
    Items for tags in ANKIHUB_TAGS_EXCLUDED_FROM_TAG_TREE are not copied to the AnkiHub tree.
    Returns True if the tag tree was built successfully, False otherwise.
    Building the tag tree can fail if related Anki functions change in the future.
    """

    # Build the tag tree using the original function used by Anki
    try:
        browser.sidebar._tag_tree(root_tree_item)
    except (AttributeError, ValueError):
        LOGGER.warning("Could not build tag tree.")
        return False

    # Move the AnkiHub tag items to the AnkiHub tree
    tag_tree = next(
        (
            item
            for item in root_tree_item.children
            if item.name == tr.browsing_sidebar_tags()
        ),
        None,
    )

    if tag_tree is None:
        LOGGER.warning("Could not find tag tree.")
        return False

    ankihub_tag_tree_items = [
        item
        for item in tag_tree.children
        if item.name.lower().startswith(ANKIHUB_TAGS_PREFIX)
        and item.name.lower() not in ANKIHUB_TAGS_EXCLUDED_FROM_TAG_TREE
    ]

    for ah_tag_tree_item in ankihub_tag_tree_items:
        tag_tree.children.remove(ah_tag_tree_item)
        ankihub_tree_item.children.append(ah_tag_tree_item)

        ah_tag_tree_item._parent_item = ankihub_tree_item
        ah_tag_tree_item.item_type = SidebarItemType.CUSTOM

        # Remove tag icons because it looks better without them
        ah_tag_tree_item.icon = ""
        for descendant in _sidebar_item_descendants(ah_tag_tree_item):
            descendant.icon = ""

    # Remove and re-add the tag tree so that the AnkiHub tag items are under the AnkiHub tree
    # and also under the tag tree
    root_tree_item.children.remove(tag_tree)
    browser.sidebar._tag_tree(root_tree_item)

    LOGGER.info("Built tag tree and copied AnkiHub tag items to AnkiHub tree.")

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
        search_node=aqt.mw.col.group_searches(
            SearchNode(parsable_text="ankihub_id:"),
            SearchNode(parsable_text=f"{AnkiHubNoteSearchNode.parameter_name}:no"),
        ),
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
        search_nodes = []

        if suggestion_type != SuggestionType.DELETE:
            search_nodes.append(SearchNode(parsable_text="ankihub_id:_*"))

        search_nodes.extend(
            [
                SearchNode(
                    parsable_text=f"{UpdatedInTheLastXDaysSearchNode.parameter_name}:1"
                ),
                SearchNode(
                    parsable_text=f"{SuggestionTypeSearchNode.parameter_name}:{suggestion_value_escaped}"
                ),
            ]
        )

        updated_today_item.add_simple(
            name=suggestion_name,
            icon="",
            type=SidebarItemType.SAVED_SEARCH,
            search_node=aqt.mw.col.group_searches(*search_nodes),
        )

    updated_since_last_review_item = result.add_simple(
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

    updated_since_last_review_item.add_simple(
        name="Deleted",
        icon="",
        type=SidebarItemType.SAVED_SEARCH,
        search_node=aqt.mw.col.group_searches(
            SearchNode(
                parsable_text=f"{UpdatedSinceLastReviewSearchNode.parameter_name}:"
            ),
            SearchNode(
                parsable_text=f"{SuggestionTypeSearchNode.parameter_name}:{SuggestionType.DELETE.value[0]}"
            ),
        ),
    )

    result.add_simple(
        name="Deleted Notes",
        icon="",
        type=SidebarItemType.SAVED_SEARCH_ROOT,
        search_node=aqt.mw.col.group_searches(
            SearchNode(
                parsable_text=f"{SuggestionTypeSearchNode.parameter_name}:{SuggestionType.DELETE.value[0]}"
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


# copy note action
def _make_copy_note_action_not_copy_ankihub_id() -> None:
    """Make the Create Copy note context menu action not copy the AnkiHub ID field."""
    original_on_create_copy = Browser.on_create_copy
    Browser.on_create_copy = wrap(  # type: ignore
        old=lambda self, *args: original_on_create_copy(self),
        new=_after_create_copy,
        pos="after",
    )


def _after_create_copy(*args, **kwargs) -> None:
    """Clear the AnkiHub ID field of the new note in the AddCards dialog that was opened
    when the Create Copy note context menu action was clicked."""
    add_cards_dialog: AddCards
    if not (add_cards_dialog := aqt.dialogs._dialogs.get("AddCards", [None, None])[1]):
        return

    note = add_cards_dialog.editor.note
    if ANKIHUB_NOTE_TYPE_FIELD_NAME in note.keys():
        note[ANKIHUB_NOTE_TYPE_FIELD_NAME] = ""
        add_cards_dialog.editor.loadNote()
