import uuid
from concurrent.futures import Future
from typing import Optional

import aqt
from aqt import dialogs
from aqt.browser import Browser

from ...main.subdecks import build_subdecks_and_move_cards_to_them, flatten_deck
from ...settings import config
from ..utils import ask_user, tooltip


def confirm_and_toggle_subdecks(ankihub_id: uuid.UUID) -> None:
    """Ask the user if they want to toggle subdecks for the given deck and do so if they confirm."""
    deck_config = config.deck_config(ankihub_id)
    using_subdecks = deck_config.subdecks_enabled

    if using_subdecks:
        if not ask_user(
            "Do you want to remove the subdecks of<br>"
            f"<b>{config.deck_config(ankihub_id).name}</b>?<br><br>"
            "<b>Warning:</b> This will remove all subdecks of this deck and move "
            "all of its cards back to the main deck.</b>"
            "<br><br>"
            "See <a href='https://docs.ankihub.net/user_docs/advanced.html#subdecks-and-subdeck-tags'>"
            "the AnkiHub docs</a> "
            "for details.",
            default_no=True,
        ):
            return

        aqt.mw.taskman.with_progress(
            label="Removing subdecks and moving cards...",
            task=lambda: flatten_deck(ankihub_id),
            on_done=_on_subdecks_updated,
        )
    else:
        if not ask_user(
            "Do you want to enable subdecks for<br>"
            f"<b>{config.deck_config(ankihub_id).name}</b>?"
            "<br><br>"
            "See <a href='https://docs.ankihub.net/user_docs/advanced.html#subdecks-and-subdeck-tags'>"
            "the AnkiHub docs</a> "
            "for details."
        ):
            return

        aqt.mw.taskman.with_progress(
            label="Building subdecks and moving cards...",
            task=lambda: build_subdecks_and_move_cards_to_them(ankihub_id),
            on_done=_on_subdecks_updated,
        )

    config.set_subdecks(ankihub_id, not using_subdecks)


def _on_subdecks_updated(future: Future):
    future.result()

    tooltip("Subdecks updated.", parent=aqt.mw)
    aqt.mw.deckBrowser.refresh()
    browser: Optional[Browser] = dialogs._dialogs["Browser"][1]
    if browser is not None:
        browser.sidebar.refresh()
