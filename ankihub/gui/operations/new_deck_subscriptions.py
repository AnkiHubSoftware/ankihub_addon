"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""

from concurrent.futures import Future
from typing import Callable, List, Optional

import aqt
from aqt.qt import QCheckBox, QDialogButtonBox, QStyle

from ... import LOGGER
from ...ankihub_client import Deck
from ...settings import config
from ..messages import messages
from ..utils import logged_into_ankiweb, show_dialog, sync_with_ankiweb
from .deck_installation import download_and_install_decks
from .utils import future_with_result, pass_exceptions_to_on_done


@pass_exceptions_to_on_done
def check_and_install_new_deck_subscriptions(
    subscribed_decks: List[Deck], on_done: Callable[[Future], None]
) -> None:
    """Check if there are any new deck subscriptions and install them if the user confirms."""

    LOGGER.info(
        "Checking and installing new deck subscriptions...",
        subscribed_deck_ah_ids=[deck.ah_did for deck in subscribed_decks],
    )

    # Check if there are any new subscriptions
    decks = _not_installed_ah_decks(subscribed_decks)
    if not decks:
        on_done(future_with_result(None))
        return

    recommended_deck_settings_cb = QCheckBox("Use recommended deck settings")
    recommended_deck_settings_cb.setChecked(True)
    recommended_deck_settings_cb.setToolTip(
        "This will modify deck settings such as daily limits, display order, "
        "and set the learn ahead limit to 0.<br>"
        "Change these settings at any time in your deck options area."
    )
    recommended_deck_settings_cb.setIcon(
        recommended_deck_settings_cb.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        )
    )

    confirmation_dialog = show_dialog(
        title="AnkiHub | Sync",
        text=messages.deck_install_confirmation(
            decks, logged_to_ankiweb=logged_into_ankiweb()
        ),
        parent=aqt.mw,
        buttons=[
            ("Skip", QDialogButtonBox.ButtonRole.RejectRole),
            ("Install", QDialogButtonBox.ButtonRole.AcceptRole),
        ],
        default_button_idx=1,
        callback=lambda button_index: _on_button_clicked(
            button_index=button_index,
            recommended_deck_settings_cb=recommended_deck_settings_cb,
            decks=decks,
            on_done=on_done,
        ),
        open_dialog=False,
    )
    confirmation_dialog_layout = confirmation_dialog.content_layout
    confirmation_dialog_layout.insertWidget(
        confirmation_dialog_layout.count() - 2,
        recommended_deck_settings_cb,
    )
    confirmation_dialog.adjustSize()
    confirmation_dialog.open()

    # This prevents the checkbox from being garbage collected too early
    confirmation_dialog.recommended_deck_settings_cb = recommended_deck_settings_cb  # type: ignore


@pass_exceptions_to_on_done
def _on_button_clicked(
    button_index: Optional[int],
    recommended_deck_settings_cb: QCheckBox,
    decks: List[Deck],
    on_done: Callable[[Future], None],
) -> None:
    if button_index != 1:
        # Skip
        LOGGER.info("User skipped deck installation.")
        on_done(future_with_result(None))
        return

    # Download the new decks
    def on_collection_sync_finished() -> None:
        ah_dids = [deck.ah_did for deck in decks]
        download_and_install_decks(
            ah_dids,
            on_done=on_done,
            recommended_deck_settings=recommended_deck_settings_cb.isChecked(),
        )

    # Sync with AnkiWeb first to avoid data loss on the next AnkiWeb sync if deck installation triggers a full sync
    sync_with_ankiweb(on_collection_sync_finished)


def _not_installed_ah_decks(subscribed_decks: List[Deck]) -> List[Deck]:
    local_deck_ids = config.deck_ids()
    result = [deck for deck in subscribed_decks if deck.ah_did not in local_deck_ids]
    return result
