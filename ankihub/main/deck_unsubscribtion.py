import uuid

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from ..settings import config
from .utils import undo_note_type_modfications


def unsubscribe_from_deck(deck_ankihub_id: uuid.UUID) -> None:
    client = AddonAnkiHubClient()
    client.unsubscribe_from_deck(deck_ankihub_id)
    config.remove_deck(deck_ankihub_id)
    mids = ankihub_db.note_types_for_ankihub_deck(deck_ankihub_id)
    undo_note_type_modfications(mids)
    ankihub_db.remove_deck(deck_ankihub_id)
    LOGGER.info(f"Unsubscribed from deck {deck_ankihub_id}")