"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""
from concurrent.futures import Future
from functools import partial
from typing import Callable, List, Optional

import aqt
from aqt.qt import QCheckBox, QDialogButtonBox

from ...ankihub_client import Deck
from ...settings import config
from ..messages import messages
from ..utils import show_dialog
from .deck_installation import download_and_install_decks
from .utils import future_with_exception, future_with_result


def check_and_install_new_deck_subscriptions(
    subscribed_decks: List[Deck], on_done: Callable[[Future], None]
) -> None:
    """Check if there are any new deck subscriptions and install them if the user confirms."""
    try:
        # Check if there are any new subscriptions
        decks = _not_installed_ah_decks(subscribed_decks)
        if not decks:
            on_done(future_with_result(None))
            return

        cleanup_cb = QCheckBox("Remove unused tags and empty cards")
        cleanup_cb.setChecked(True)

        recommended_deck_settings_cb = QCheckBox("Use recommended deck settings")
        recommended_deck_settings_cb.setChecked(True)

        confirmation_dialog = show_dialog(
            title="AnkiHub | Sync",
            text=messages.deck_install_confirmation(decks),
            parent=aqt.mw,
            buttons=[
                ("Skip", QDialogButtonBox.ButtonRole.RejectRole),
                ("Install", QDialogButtonBox.ButtonRole.AcceptRole),
            ],
            default_button_idx=1,
            callback=lambda button_index: _on_button_clicked(
                button_index=button_index,
                cleanup_cb=cleanup_cb,
                recommended_deck_settings_cb=recommended_deck_settings_cb,
                decks=decks,
                on_done=on_done,
            ),
            open_dialog=False,
        )
        confirmation_dialog_layout = confirmation_dialog.content_layout
        confirmation_dialog_layout.insertWidget(
            confirmation_dialog_layout.count() - 2,
            cleanup_cb,
        )
        confirmation_dialog_layout.insertWidget(
            confirmation_dialog_layout.count() - 1,
            recommended_deck_settings_cb,
        )
        confirmation_dialog.open()

        # This prevents the checkbox from being garbage collected too early
        confirmation_dialog.cleanup_cb = cleanup_cb  # type: ignore
    except Exception as e:
        # Using run_on_main prevents exceptions which occur in the callback to be backpropagated to the caller,
        # which is what we want.
        aqt.mw.taskman.run_on_main(partial(on_done, future_with_exception(e)))


def _on_button_clicked(
    button_index: Optional[int],
    cleanup_cb: QCheckBox,
    recommended_deck_settings_cb: QCheckBox,
    decks: List[Deck],
    on_done: Callable[[Future], None],
) -> None:
    if button_index != 1:
        # Skip
        on_done(future_with_result(None))
        return

    # Download the new decks
    try:
        ah_dids = [deck.ah_did for deck in decks]
        download_and_install_decks(
            ah_dids,
            on_done=on_done,
            cleanup=cleanup_cb.isChecked(),
            recommended_deck_settings=recommended_deck_settings_cb.isChecked(),
        )
    except Exception as e:
        on_done(future_with_exception(e))


def _not_installed_ah_decks(subscribed_decks: List[Deck]) -> List[Deck]:
    local_deck_ids = config.deck_ids()
    result = [deck for deck in subscribed_decks if deck.ah_did not in local_deck_ids]
    return result
