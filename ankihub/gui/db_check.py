from typing import List

from aqt.utils import askUser

from .. import LOGGER
from ..db import ankihub_db
from ..settings import config
from .decks import SubscribeDialog, cleanup_after_deck_install


def check_ankihub_db():
    dids_with_missing_values = ankihub_db.ankihub_dids_of_decks_with_missing_values()

    if not dids_with_missing_values:
        LOGGER.debug("No decks with missing values found.")
        return

    LOGGER.debug(f"Decks with missing values found: {dids_with_missing_values}")

    deck_names = sorted(
        [
            config.private_config.decks[deck_id]["name"]
            for deck_id in dids_with_missing_values
        ],
        key=str.lower,
    )

    if askUser(
        text=(
            "AnkiHub has detected that the following deck(s) have missing values in the database:<br>"
            f"{'<br>'.join('<b>' + deck_name + '</b>' for deck_name in deck_names)}<br><br>"
            "The add-on needs to do a full sync of these decks. This may take a while.<br><br>"
            "Do you want to do the full sync now?"
        )
    ):
        download_and_install_decks(dids_with_missing_values)


def download_and_install_decks(ankihub_dids: List[str]):
    # Installs decks one by one and then cleans up.
    # If a deck install fails, the other deck installs and the cleanup are **not** executed.
    # (The cleanup is not essential, it's just nice to have.)
    if not ankihub_dids:
        cleanup_after_deck_install(multiple_decks=True)
        return

    cur_did = ankihub_dids.pop()

    subscribe_dialog = SubscribeDialog()
    subscribe_dialog.hide()

    subscribe_dialog.download_and_install_deck(
        cur_did,
        on_success=lambda: download_and_install_decks(ankihub_dids),
        cleanup=False,
    )
