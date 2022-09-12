from typing import Optional

from aqt import mw
from aqt.browser import Browser
from aqt.gui_hooks import browser_will_show_context_menu
from aqt.operations import CollectionOp
from aqt.qt import QMenu
from aqt.utils import askUser, getTag, tooltip, tr

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..suggestions import suggest_notes_in_bulk
from ..utils import ankihub_uuids_of_nids


def on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    if browser.table.is_notes_mode():
        menu = context_menu
    else:
        notes_submenu: Optional[QMenu] = next(
            (
                menu  # type: ignore
                for menu in context_menu.findChildren(QMenu)
                if menu.title() == tr.qt_accel_notes()  # type: ignore
            ),
            None,
        )
        if notes_submenu is None:
            return
        menu = notes_submenu

    menu.addSeparator()
    menu.addAction(
        "AnkiHub: Bulk suggest notes",
        lambda: on_bulk_notes_suggest_action(browser),
    )
    menu.addAction(
        "AnkiHub: Bulk suggest tags",
        lambda: on_bulk_tag_suggestion_action(browser),
    )


def on_bulk_notes_suggest_action(browser: Browser):
    selected_nids = browser.selected_notes()
    notes = [mw.col.get_note(selected_nid) for selected_nid in selected_nids]

    auto_accept = askUser("Do you want to submit these notes without review?")
    suggest_notes_in_bulk(notes, auto_accept=auto_accept)

    LOGGER.debug("Bulk note suggestion created.")
    tooltip("Bulk note suggestion created.", parent=browser)


def on_bulk_tag_suggestion_action(browser: Browser) -> None:
    selected_nids = browser.selected_notes()
    (tags, ok) = getTag(
        parent=browser,
        deck=mw.col,
        question="Enter space-separated list of tags to add to selected notes.\n\n"
        "* Tags will be added to notes that don't already have them.",
    )

    if not ok:
        return

    ankihub_note_uuids = ankihub_uuids_of_nids(selected_nids, ignore_invalid=True)

    if not ankihub_note_uuids:
        tooltip("No AnkiHub notes were selected.")
        return

    client = AnkiHubClient()
    client.bulk_suggest_tags(ankihub_note_uuids, tags.split(" "))
    LOGGER.debug("Bulk tag suggestion created.")
    tooltip("Bulk tag suggestion created.", parent=browser)

    # using this instead of browser.add_tags_to_notes to avoid tooltip conflict
    # there will be no exception when adding the tags fails, this should not be a big problem
    op = (
        CollectionOp(browser, lambda col: col.tags.bulk_add(selected_nids, tags))
        .success(
            lambda out: LOGGER.debug(
                "Added chosen tags to selected notes if they didn't have them yet."
            )
        )
        .failure(
            lambda exc: LOGGER.debug("Failed to add chosen tags to selected notes.")
        )
    )
    op.run_in_background()


def setup() -> None:
    browser_will_show_context_menu.append(on_browser_will_show_context_menu)
