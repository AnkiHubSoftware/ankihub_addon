import uuid
from typing import Callable, List, Optional

from .... import LOGGER
from ....db import ankihub_db
from ....settings import config
from ...decks_dialog import download_and_install_decks
from ...utils import ask_user


def check_ankihub_db(on_success: Optional[Callable[[], None]] = None):
    ah_dids_with_missing_values = ankihub_db.ankihub_dids_of_decks_with_missing_values()
    ah_dids_missing_from_config = _decks_missing_from_config()
    ah_dids_with_something_missing = list(
        set(ah_dids_with_missing_values) | set(ah_dids_missing_from_config)
    )

    if not ah_dids_with_something_missing:
        LOGGER.info("No decks with something missing found.")
        on_success()
        return

    LOGGER.info(f"Decks with missing values found: {ah_dids_with_missing_values}")
    LOGGER.info(f"Decks missing from config found: {ah_dids_missing_from_config}")

    if ah_dids_missing_from_config:
        messsage_begin = "AnkiHub has detected that some decks have missing values in the database.<br><br>"
    else:
        deck_names = sorted(
            [
                config.deck_config(deck_id).name
                for deck_id in ah_dids_with_missing_values
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
        download_and_install_decks(
            ah_dids_with_something_missing, on_success=on_success
        )


def _decks_missing_from_config() -> List[uuid.UUID]:
    ah_dids_from_ankihub_db = ankihub_db.ankihub_deck_ids()
    ah_dids_from_config = config.deck_ids()
    return list(set(ah_dids_from_ankihub_db) - set(ah_dids_from_config))