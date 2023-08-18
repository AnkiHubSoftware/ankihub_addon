import uuid
from typing import Callable, List, Optional

from .... import LOGGER
from ....addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ....db import ankihub_db
from ....settings import config
from ...decks_dialog import download_and_install_decks
from ...utils import ask_user


def check_ankihub_db(on_success: Optional[Callable[[], None]] = None) -> None:
    _fetch_missing_note_types()

    if not _fetch_missing_notes(on_success=on_success):
        on_success()


def _fetch_missing_note_types() -> None:
    """Fetches note types which are missing from the database from AnkiHub.
    This is necessary because in a previous version of the add-on, note types were not saved in the database."""
    client = AnkiHubClient()
    for ah_did in ankihub_db.ankihub_deck_ids():
        mids = ankihub_db.list(
            """
            SELECT DISTINCT anki_note_type_id FROM notes WHERE ankihub_deck_id = ?
            """,
            str(ah_did),
        )
        mids_of_missing_note_types = [
            mid
            for mid in mids
            if not ankihub_db.note_type_dict(ankihub_did=ah_did, note_type_id=mid)
        ]
        if not mids_of_missing_note_types:
            continue

        LOGGER.info(
            f"Missing note types found for deck {ah_did}: {mids_of_missing_note_types}"
        )

        for mid in mids_of_missing_note_types:
            note_type = client.get_note_type(anki_note_type_id=mid)
            ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=note_type)

        LOGGER.info(
            f"Missing note types for deck {ah_did} have been fetched from AnkiHub."
        )


def _fetch_missing_notes(on_success: Optional[Callable[[], None]] = None) -> bool:
    """Fetches notes data which is missing from the database from AnkiHub.
    Returns True if the user has chosen to fix the missing notes and on_success will be called, False otherwise."""
    ah_dids_with_missing_values = ankihub_db.ankihub_dids_of_decks_with_missing_values()
    ah_dids_missing_from_config = _decks_missing_from_config()
    ah_dids_with_something_missing = list(
        set(ah_dids_with_missing_values) | set(ah_dids_missing_from_config)
    )

    if not ah_dids_with_something_missing:
        LOGGER.info("No decks with something missing found.")
        return False

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
            ah_dids_with_something_missing, on_done=lambda _: on_success()
        )
        return True

    return False


def _decks_missing_from_config() -> List[uuid.UUID]:
    ah_dids_from_ankihub_db = ankihub_db.ankihub_deck_ids()
    ah_dids_from_config = config.deck_ids()
    return list(set(ah_dids_from_ankihub_db) - set(ah_dids_from_config))
