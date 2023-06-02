"""Code for downloading and installing decks in the background and showing the related dialogs
(install confirmation dialog, import summary dialog, etc.)."""
import uuid
from concurrent.futures import Future
from datetime import datetime
from typing import Callable, List

import aqt
from aqt.emptycards import show_empty_cards
from aqt.operations.tag import clear_unused_tags
from aqt.utils import showInfo, showText

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import NoteInfo
from ...ankihub_client.ankihub_client import AnkiHubHTTPError
from ...ankihub_client.models import UserDeckRelation
from ...importing import AnkiHubImporter, AnkiHubImportResult
from ...media_sync import media_sync
from ...settings import config, url_view_deck
from ...subdecks import deck_contains_subdeck_tags
from ...utils import create_backup
from ..exceptions import DeckDownloadAndInstallError
from ..messages import messages
from ..utils import ask_user
from .subdecks import confirm_and_toggle_subdecks


def download_and_install_decks(
    ankihub_dids: List[uuid.UUID], on_success: Callable[[], None]
) -> None:
    """Downloads and installs the given decks in the background.
    Shows an import summary once the decks are installed.
    Calls on_success when done."""

    def on_install_done(future: Future):
        try:
            import_results: List[AnkiHubImportResult] = future.result()
        except DeckDownloadAndInstallError as e:
            if _maybe_handle_deck_download_and_install_error(e):
                return
            else:
                raise e

        # Clean up after deck installations
        _cleanup_after_deck_install(multiple_decks=len(import_results) > 1)

        # Reset the main window
        aqt.mw.reset()

        # Ask user to enable subdecks if available for each deck that was installed.
        for import_result in import_results:
            ah_did = import_result.ankihub_did
            if deck_contains_subdeck_tags(ah_did):
                confirm_and_toggle_subdecks(ah_did)

        # Show import result message
        # ... Anki deck names can be different from AnkiHub deck names, so we need to look them up.
        ankihub_deck_names = [
            config.deck_config(ah_did).name for ah_did in ankihub_dids
        ]
        anki_deck_names = [
            aqt.mw.col.decks.name(config.deck_config(ah_did).anki_id)
            for ah_did in ankihub_dids
        ]
        message = messages.deck_import_summary(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=import_results,
        )
        showInfo(
            title="AnkiHub Deck Import Summary",
            text=message,
            textFormat="rich",
        )

        on_success()

    # Install decks in background
    aqt.mw.taskman.with_progress(
        task=lambda: _download_and_install_decks_inner(ankihub_dids),
        on_done=on_install_done,
        label="Downloading decks from AnkiHub",
    )


def _maybe_handle_deck_download_and_install_error(
    e: DeckDownloadAndInstallError,
) -> bool:
    """Checks the given exception and handles it if it's a known error. Returns True if the error
    was handled, False otherwise.
    This function is only used for the old subscription workflow.
    In the new workflow this is not needed, because users can't try to install deck that they
    are not subscribed to."""
    if AnkiHubClient().is_feature_flag_enabled("new_subscription_workflow_enabled"):
        return False

    if not isinstance(e.original_exception, AnkiHubHTTPError):
        return False

    http_error: AnkiHubHTTPError = e.original_exception

    if http_error.response.status_code == 404:
        showText(
            f"Deck {e.ankihub_did} doesn't exist. Please make sure to copy/paste "
            f"the correct ID. If you believe this is an error, please reach "
            f"out to user support at help@ankipalace.com."
        )
        return True
    elif http_error.response.status_code == 403:
        deck_url = f"{url_view_deck()}{e.ankihub_did}"
        showInfo(
            f"Please first subscribe to the deck on the AnkiHub website.<br>"
            f"Link to the deck: <a href='{deck_url}'>{deck_url}</a><br>"
            "<br>"
            "Note that you also need an active AnkiHub subscription.<br>"
            "You can get a subscription at<br>"
            "<a href='https://www.ankihub.net/'>https://www.ankihub.net/</a>",
        )
        return True

    return False


def _download_and_install_decks_inner(
    ankihub_dids: List[uuid.UUID],
) -> List[AnkiHubImportResult]:
    """Downloads and installs the given decks.
    Attempts to install all decks even if some fail."""
    result = []
    exceptions = []
    for ah_did in ankihub_dids:
        try:
            result.append(_download_and_install_single_deck(ah_did))
        except Exception as e:
            exceptions.append(
                DeckDownloadAndInstallError(
                    original_exception=e,
                    ankihub_did=ah_did,
                )
            )
            LOGGER.warning(f"Failed to download and install deck {ah_did}.", exc_info=e)

    if exceptions:
        # Raise the first exception that occurred
        raise exceptions[0]

    return result


def _download_and_install_single_deck(ankihub_did: uuid.UUID) -> AnkiHubImportResult:
    deck = AnkiHubClient().get_deck_by_id(ankihub_did)
    notes_data: List[NoteInfo] = AnkiHubClient().download_deck(
        deck.ankihub_deck_uuid, download_progress_cb=_download_progress_cb
    )

    aqt.mw.taskman.run_on_main(
        lambda: aqt.mw.progress.update(label="Installing deck...", max=0, value=0)
    )
    result = _install_deck(
        notes_data=notes_data,
        deck_name=deck.name,
        ankihub_did=deck.ankihub_deck_uuid,
        user_relation=deck.user_relation,
        latest_update=deck.csv_last_upload,
    )

    return result


def _install_deck(
    notes_data: List[NoteInfo],
    deck_name: str,
    ankihub_did: uuid.UUID,
    user_relation: UserDeckRelation,
    latest_update: datetime,
) -> AnkiHubImportResult:
    """Imports the notes_data into the Anki collection.
    Saves the deck subscription to the config file.
    Starts the media download.
    Returns information about the import.
    """
    create_backup()

    importer = AnkiHubImporter()
    import_result = importer.import_ankihub_deck(
        ankihub_did=ankihub_did,
        notes_data=notes_data,
        deck_name=deck_name,
    )

    config.save_subscription(
        name=deck_name,
        ankihub_did=ankihub_did,
        anki_did=import_result.anki_did,
        user_relation=user_relation,
        latest_udpate=latest_update,
    )

    media_sync.start_media_download()

    LOGGER.info("Importing deck was succesful.")

    return import_result


def _download_progress_cb(percent: int):
    # adding +1 to avoid progress increasing while at 0% progress
    # (the aqt.mw.progress.update function does that)
    aqt.mw.taskman.run_on_main(
        lambda: aqt.mw.progress.update(
            label="Downloading deck...",
            value=percent + 1,
            max=101,
        )
    )


def _cleanup_after_deck_install(multiple_decks: bool) -> None:
    message = (
        (
            "The deck has been successfully installed!<br><br>"
            if not multiple_decks
            else "The decks have been successfully installed!<br><br>"
        )
        + "Do you want to clear unused tags and empty cards from your collection? (recommended)"
    )
    if ask_user(message, title="AnkiHub", show_cancel_button=False):
        clear_unused_tags(parent=aqt.mw).run_in_background()
        show_empty_cards(aqt.mw)
