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
        lambda: on_bulk_tag_suggestion_action(browser),
    )


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

    ankihub_note_uuids = []
    for nid in selected_nids:
        note = mw.col.get_note(nid)
        if ANKIHUB_NOTE_TYPE_FIELD_NAME not in note.keys():
            continue

        try:
            ankihub_note_uuid = uuid.UUID(note[ANKIHUB_NOTE_TYPE_FIELD_NAME])
        except ValueError:
            continue

        ankihub_note_uuids.append(ankihub_note_uuid)

    client = AnkiHubClient()
    response = client.bulk_suggest_tags(ankihub_note_uuids, tags.split(" "))
    if response.status_code != 201:
        LOGGER.debug("Bulk tag suggestion failed.")
        return

    LOGGER.debug("Bulk tag suggestion created.")
    tooltip("Bulk tag suggestion created.")

    browser.add_tags_to_selected_notes(tags)
    LOGGER.debug("Added chosen tags to selected notes if they didn't have them yet.")


def setup() -> None:
    browser_will_show_context_menu.append(on_browser_will_show_context_menu)
