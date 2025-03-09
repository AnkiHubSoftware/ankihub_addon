import uuid
from concurrent.futures import Future
from typing import Callable, List, Optional

from .... import LOGGER
from ....db import ankihub_db
from ....main.deck_unsubscribtion import uninstall_deck
from ....settings import config
from ...exceptions import DeckDownloadAndInstallError, RemoteDeckNotFoundError
from ...operations.deck_installation import download_and_install_decks
from ...utils import ask_user


def check_ankihub_db(on_success: Optional[Callable[[], None]] = None) -> None:
    LOGGER.info("Checking AnkiHub database...")

    if not _try_reinstall_decks_with_something_missing(on_success=on_success):
        on_success()


def _try_reinstall_decks_with_something_missing(
    on_success: Optional[Callable[[], None]] = None,
) -> bool:
    """Checks which decks have missing values in the AnkiHubDB or are missing from the config and asks the user
    if they want to fix the missing values by reinstalling the decks.
    If the decks are not found on AnkiHub anymore, they are uninstalled instead.
    Returns True if the user has chosen to run the fix, False otherwise."""
    ah_dids_with_missing_values = ankihub_db.ankihub_dids_of_decks_with_missing_values()
    ah_dids_missing_from_config = _decks_missing_from_config()
    ah_dids_with_something_missing = list(
        set(ah_dids_with_missing_values) | set(ah_dids_missing_from_config)
    )

    if not ah_dids_with_something_missing:
        LOGGER.info("No decks with something missing found.")
        return False

    LOGGER.info(
        "Decks with something missing found.",
        ah_dids_with_missing_values=ah_dids_with_missing_values,
        ah_dids_missing_from_config=ah_dids_missing_from_config,
    )

    if ah_dids_missing_from_config:
        messsage_begin = "AnkiHub has detected that some decks have missing values in the database.<br><br>"
    else:
        deck_names = sorted(
            [
                deck_config.name
                for deck_id in ah_dids_with_missing_values
                if (deck_config := config.deck_config(deck_id)) is not None
            ],
            key=str.lower,
        )
        messsage_begin = (
            "AnkiHub has detected that the following deck(s) have missing values in the database:<br>"
            f"{'<br>'.join('<b>' + deck_name + '</b>' for deck_name in deck_names)}<br><br>"
        )

    if ask_user(
        text=(
            messsage_begin
            + "The add-on needs to download and import these decks again. This may take a while.<br><br>"
            "A full sync with AnkiWeb might be necessary after the reset, so it's recommended "
            "to sync changes from other devices before doing this.<br><br>"
            "Do you want to fix the missing values now?"
        ),
        title="AnkiHub Database Check",
    ):

        def on_download_and_install_done(future: Future) -> None:
            try:
                future.result()
            except DeckDownloadAndInstallError as e:
                if isinstance(e.original_exception, RemoteDeckNotFoundError):
                    LOGGER.info(
                        "Deck not found on AnkiHub anymore. Uninstalling it.",
                        ah_did=e.ankihub_did,
                    )
                    uninstall_deck(e.ankihub_did)
                else:
                    raise e

            on_success()

        download_and_install_decks(
            ah_dids_with_something_missing, on_done=on_download_and_install_done
        )
        return True

    return False


def _decks_missing_from_config() -> List[uuid.UUID]:
    ah_dids_from_ankihub_db = ankihub_db.ankihub_dids()
    ah_dids_from_config = config.deck_ids()
    return list(set(ah_dids_from_ankihub_db) - set(ah_dids_from_config))
