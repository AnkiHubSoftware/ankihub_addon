import uuid

from aqt import mw
from aqt.browser import Browser
from aqt.gui_hooks import browser_will_show_context_menu
from aqt.qt import QMenu
from aqt.utils import getTag, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..constants import ANKIHUB_NOTE_TYPE_FIELD_NAME


def on_browser_will_show_context_menu(browser: Browser, context_menu: QMenu) -> None:
    if not browser.table.is_notes_mode():
        return

    context_menu.addSeparator()
    context_menu.addAction(
        "AnkiHub: Bulk suggest tags",
        lambda: on_context_menu_action(browser),
    )


def on_context_menu_action(browser: Browser) -> None:
    selected_nids = browser.selected_notes()
    (tags, ok) = getTag(
        parent=browser,
        deck=mw.col,
        question="What tags do you want to suggest to be added to the selected notes?",
    )

    if not ok:
        return

    ankihub_note_uuids = []
    for nid in selected_nids:
        note = mw.col.get_note(nid)
        if ANKIHUB_NOTE_TYPE_FIELD_NAME not in note.keys():
            continue
        ankihub_note_uuid = note[ANKIHUB_NOTE_TYPE_FIELD_NAME]
        ankihub_note_uuids.append(uuid.UUID(ankihub_note_uuid))

    client = AnkiHubClient()
    response = client.bulk_suggest_tags(ankihub_note_uuids, tags.split(" "))
    if response.status_code != 201:
        LOGGER.debug("Bulk tag suggestion failed.")
        return

    LOGGER.debug("Bulk tag suggestion created.")
    tooltip("Bulk tag suggestion created.")


def setup() -> None:
    browser_will_show_context_menu.append(on_browser_will_show_context_menu)
