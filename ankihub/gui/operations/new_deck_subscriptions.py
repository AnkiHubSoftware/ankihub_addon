"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""
from concurrent.futures import Future
from typing import Callable, List

import aqt
from aqt.qt import QCheckBox, QDialogButtonBox, Qt
from aqt.utils import MessageBox

from ...ankihub_client import Deck
from ...main.importing import AnkiHubImportResult
from ...settings import config
from ..messages import messages
from ..utils import show_dialog, tooltip_icon
from .deck_installation import download_and_install_decks
from .utils import future_with_exception, future_with_result


def check_and_install_new_deck_subscriptions(
    subscribed_decks: List[Deck],
    on_done: Callable[[Future[List[AnkiHubImportResult]]], None],
) -> None:
    """Check if there are any new deck subscriptions and install them if the user confirms.
    Show a import summary dialog after the decks are installed."""
    try:
        # Check if there are any new subscriptions
        decks = _not_installed_ah_decks(subscribed_decks)
        if not decks:
            on_done(future_with_result(None))
            return

        cleanup_cb = QCheckBox("Remove unused tags and empty cards")
        cleanup_cb.setChecked(True)

        confirmation_dialog = MessageBox(
            title="AnkiHub | Sync",
            text=messages.deck_install_confirmation(decks),
            textFormat=Qt.TextFormat.RichText,
            parent=aqt.mw,
            buttons=["Skip", "Install"],
            default_button=1,
            callback=lambda button_index: _on_button_clicked(
                button_index=button_index,
                cleanup_cb=cleanup_cb,
                decks=decks,
                on_done=on_done,
            ),
        )

        confirmation_dialog.setCheckBox(cleanup_cb)

        # This prevents the checkbox from being garbage collected too early
        confirmation_dialog.cleanup_cb = cleanup_cb  # type: ignore
    except Exception as e:
        on_done(future_with_exception(e))


def _on_button_clicked(
    button_index: int,
    cleanup_cb: QCheckBox,
    decks: List[Deck],
    on_done: Callable[[Future[List[AnkiHubImportResult]]], None],
) -> None:
    if button_index == 0:
        # Skip
        on_done(future_with_result(None))
        return

    # Download the new decks
    try:
        ah_dids = [deck.ah_did for deck in decks]
        download_and_install_decks(
            ah_dids,
            on_done=lambda future: _on_decks_installed(future=future, on_done=on_done),
            cleanup=cleanup_cb.isChecked(),
        )
    except Exception as e:
        on_done(future_with_exception(e))


def _on_decks_installed(future: Future, on_done: Callable[[Future], None]):
    try:
        import_results: List[AnkiHubImportResult] = future.result()
    except Exception as e:
        on_done(future_with_exception(e))
        return

    _show_deck_import_summary_dialog(
        import_results=import_results,
        on_done=lambda: on_done(future_with_result(None)),
    )


def _show_deck_import_summary_dialog(
    import_results: List[AnkiHubImportResult], on_done: Callable[[], None]
) -> None:
    ankihub_dids = [import_result.ankihub_did for import_result in import_results]
    ankihub_deck_names = [config.deck_config(ah_did).name for ah_did in ankihub_dids]
    anki_deck_names = [
        aqt.mw.col.decks.name(config.deck_config(ah_did).anki_id)
        for ah_did in ankihub_dids
    ]
    message = messages.deck_import_summary(
        ankihub_deck_names=ankihub_deck_names,
        anki_deck_names=anki_deck_names,
        import_results=import_results,
    )

    show_dialog(
        message,
        title="AnkiHub | Deck Import Summary",
        buttons=[QDialogButtonBox.StandardButton.Ok],
        default_button_idx=0,
        scrollable=True,
        icon=tooltip_icon(),
        callback=lambda _: on_done(),
    )


def _not_installed_ah_decks(subscribed_decks: List[Deck]) -> List[Deck]:
    local_deck_ids = config.deck_ids()
    result = [deck for deck in subscribed_decks if deck.ah_did not in local_deck_ids]
    return result
