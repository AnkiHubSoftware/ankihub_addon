import uuid
from typing import Any, Optional
from uuid import UUID

import aqt
from aqt import QDialogButtonBox, sip
from aqt.utils import openLink

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..gui.webview import AnkiHubWebViewDialog
from ..product_metrics_client import (
    ProductMetricsClient,
    ProductMetricsHTTPError,
    ProductMetricsRequestException,
)
from ..settings import (
    config,
    url_flashcard_selector,
    url_flashcard_selector_embed,
    url_plans_page,
)
from ..user_state import check_user_feature_access
from .operations import AddonQueryOp
from .utils import show_dialog


class FlashCardSelectorDialog(AnkiHubWebViewDialog):
    dialog: Optional["FlashCardSelectorDialog"] = None

    def __init__(self, ah_did: uuid.UUID, parent) -> None:
        super().__init__(parent)

        self.ah_did = ah_did

    @classmethod
    def display_for_ah_did(cls, ah_did: uuid.UUID, parent: Any) -> "FlashCardSelectorDialog":
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
        if config.get_feature_flags().get("smart_search_revamp", False):
            self.setWindowTitle("AnkiHub | Smart Search")
        else:
            self.setWindowTitle("AnkiHub | Flashcard Selector")
        self.resize(1000, 800)

        super()._setup_ui()

    def _get_embed_url(self) -> str:
        return url_flashcard_selector_embed(self.ah_did)

    def _get_non_embed_url(self) -> str:
        return url_flashcard_selector(self.ah_did)


UPSELL_SOURCE_TRIAL_ENDED = "trial_ended"
UPSELL_SOURCE_GENERIC = "generic_upsell"

# Third value in the web's `surface` taxonomy, alongside `web_app` (browser) and
# `anki_webview` (webview hosted in Anki), which are set in _analytics_state.html:17.
# Note that Smart Search's webview is also served inside the add-on and reports
# `anki_webview`, so this value means specifically the add-on's native Qt layer.
# Both surfaces open the same plans page with no marker of their own, so this property
# is the only thing preventing double-counting.
UPSELL_SURFACE = "anki_addon"


def _track_upsell_event(event_name: str, source: str) -> None:
    """Report an upsell event to the product metrics collector.

    Fire-and-forget: a failed or slow send must never affect the dialog.
    """
    user_id = str(config.user_id())
    plan = config.plan()
    is_staff = config.is_staff()
    is_admin = config.is_admin()
    is_beta_tester = config.is_beta_tester()

    def send_event() -> None:
        try:
            ProductMetricsClient(url=config.product_metrics_url).track(
                distinct_id=user_id,
                event_name=event_name,
                properties={
                    # Key and values mirror the web upsell's taxonomy so the two channels can
                    # be reconciled later (ModalUpsellContent.html:40,47 emits `source`).
                    "source": source,
                    "surface": UPSELL_SURFACE,
                    "user": user_id,
                    "plan": plan,
                    "is_staff_or_admin": is_staff or is_admin,
                    "beta_tester": is_beta_tester,
                },
            )
        except (ProductMetricsHTTPError, ProductMetricsRequestException) as exc:
            LOGGER.warning(
                "failed_to_track_upsell_event",
                event_name=event_name,
                exception=str(exc),
            )

    aqt.mw.taskman.run_in_background(send_event)


def _display_upsell(source: str, parent=aqt.mw) -> None:
    text = "Let AI do the heavy lifting! Find flashcards perfectly matched to your study materials and elevate your \
learning experience with Premium. 🌟"
    if source == UPSELL_SOURCE_TRIAL_ENDED:
        title = "Your Trial Has Ended! 🎓✨"
    else:
        title = "📚 Unlock Your Potential with Premium"

    def on_button_clicked(button_index: int) -> None:
        if button_index == 1:
            _track_upsell_event("upgrade_cta_clicked", source)
            openLink(url_plans_page())

    show_dialog(
        text,
        title,
        parent=parent,
        buttons=[
            ("Not Now", QDialogButtonBox.ButtonRole.RejectRole),
            ("Learn More", QDialogButtonBox.ButtonRole.HelpRole),
        ],
        default_button_idx=1,
        callback=on_button_clicked,
    )
    _track_upsell_event("upgrade_cta_viewed", source)


def _show_upsell(user_details: dict, parent=aqt.mw) -> None:
    """Claim the one-shot trial-ended message, then show the upsell with the matching copy.

    The claim is what marks the message as shown, so it is sent here - at display time - and
    never as a side effect of reading user state. A failed claim degrades to the generic copy
    instead of surfacing an error; AddonQueryOp re-raises to the central error handler by
    default, so the failure handler below is required rather than optional.
    """

    def on_claimed(show_trial_ended_message: bool) -> None:
        source = UPSELL_SOURCE_TRIAL_ENDED if show_trial_ended_message else UPSELL_SOURCE_GENERIC
        _display_upsell(source, parent)

    def on_failure(exc: Exception) -> None:
        LOGGER.warning("failed_to_claim_trial_ended_message", exception=str(exc))
        _display_upsell(UPSELL_SOURCE_GENERIC, parent)

    (
        AddonQueryOp(
            op=lambda _: AnkiHubClient().claim_trial_ended_message(),
            success=on_claimed,
            parent=parent or aqt.mw,
        )
        .without_collection()
        .failure(on_failure)
        .run_in_background()
    )


def show_flashcard_selector(ah_did: UUID, parent=aqt.mw) -> None:
    def on_access_granted(_: dict) -> None:
        FlashCardSelectorDialog.display_for_ah_did(ah_did=ah_did, parent=parent)
        LOGGER.info("Opened flashcard selector dialog.")

    check_user_feature_access(
        feature_key="has_flashcard_selector_access",
        on_access_granted=on_access_granted,
        on_access_denied=lambda user_details: _show_upsell(user_details, parent),
    )
