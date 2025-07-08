import uuid
from typing import Any, Optional
from uuid import UUID

import aqt
from aqt import QDialogButtonBox, sip
from aqt.utils import openLink

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..gui.webview import AnkiHubWebViewDialog
from ..settings import (
    url_flashcard_selector,
    url_flashcard_selector_embed,
    url_plans_page,
)
from .operations import AddonQueryOp
from .utils import show_dialog


class FlashCardSelectorDialog(AnkiHubWebViewDialog):

    dialog: Optional["FlashCardSelectorDialog"] = None

    def __init__(self, ah_did: uuid.UUID, parent) -> None:
        super().__init__(parent)

        self.ah_did = ah_did

    @classmethod
    def display_for_ah_did(
        cls, ah_did: uuid.UUID, parent: Any
    ) -> "FlashCardSelectorDialog":
        """Display the flashcard selector dialog for the given deck.
        Reuses the dialog if it is already open for the same deck.
        Otherwise, closes the existing dialog and opens a new one."""
        if cls.dialog and cls.dialog.ah_did != ah_did and not sip.isdeleted(cls.dialog):
            cls.dialog.close()
            cls.dialog = None

        if not cls.dialog or sip.isdeleted(cls.dialog):
            cls.dialog = cls(ah_did=ah_did, parent=parent)

        if not cls.dialog.display():
            cls.dialog = None

        return cls.dialog

    def _setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub | Flashcard Selector")
        self.resize(1000, 800)

        super()._setup_ui()

    def _get_embed_url(self) -> str:
        return url_flashcard_selector_embed(self.ah_did)

    def _get_non_embed_url(self) -> str:
        return url_flashcard_selector(self.ah_did)


def _show_upsell(user_details: dict) -> None:
    show_trial_ended_message = user_details["show_trial_ended_message"]
    text = "Let AI do the heavy lifting! Find flashcards perfectly matched to your study materials and elevate your \
learning experience with Premium. 🌟"
    if show_trial_ended_message:
        title = "Your Trial Has Ended! 🎓✨"
    else:
        title = "📚 Unlock Your Potential with Premium"

    def on_button_clicked(button_index: int) -> None:
        if button_index == 1:
            openLink(url_plans_page())

    show_dialog(
        text,
        title,
        parent=aqt.mw,
        buttons=[
            ("Not Now", QDialogButtonBox.ButtonRole.RejectRole),
            ("Learn More", QDialogButtonBox.ButtonRole.HelpRole),
        ],
        default_button_idx=1,
        callback=on_button_clicked,
    )


def show_flashcard_selector(ah_did: UUID, parent=aqt.mw) -> None:
    def fetch_user_details(_) -> dict:
        user_details = AnkiHubClient().get_user_details()
        return user_details

    def on_fetched_user_details(user_details: dict) -> None:
        if user_details.get("has_flashcard_selector_access"):
            FlashCardSelectorDialog.display_for_ah_did(ah_did=ah_did, parent=parent)
            LOGGER.info("Opened flashcard selector dialog.")
        else:
            _show_upsell(user_details)

    AddonQueryOp(
        op=fetch_user_details,
        success=on_fetched_user_details,
        parent=aqt.mw,
    ).without_collection().run_in_background()
