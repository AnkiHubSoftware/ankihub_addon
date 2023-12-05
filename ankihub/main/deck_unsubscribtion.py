import uuid

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from ..settings import config
from .utils import undo_note_type_modfications


def unsubscribe_from_deck_and_uninstall(ah_did: uuid.UUID) -> None:
    client = AddonAnkiHubClient()
    client.unsubscribe_from_deck(ah_did)
    uninstall_deck(ah_did)
    LOGGER.info(f"Unsubscribed from deck {ah_did}")


def uninstall_deck(ah_did: uuid.UUID) -> None:
    for deck_extension_id in config.deck_extensions_ids_for_ah_did(ah_did):
        config.remove_deck_extension(deck_extension_id)
    LOGGER.info(f"Removed deck extensions for deck {ah_did}")

    config.remove_deck(ah_did)
    mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
    undo_note_type_modfications(mids)
    ankihub_db.remove_deck(ah_did)
    LOGGER.info(f"Uninstalled deck {ah_did}")
