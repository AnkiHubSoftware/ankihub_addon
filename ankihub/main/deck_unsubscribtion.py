import uuid

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from ..settings import config
from .utils import (
    collection_schema,
    new_schema_to_do_full_upload_for_once,
    undo_note_type_modfications,
)


def unsubscribe_from_deck_and_uninstall(ah_did: uuid.UUID) -> None:
    client = AddonAnkiHubClient()
    client.unsubscribe_from_deck(ah_did)
    uninstall_deck(ah_did)
    LOGGER.info(f"Unsubscribed from deck {ah_did}")


def uninstall_deck(ah_did: uuid.UUID) -> None:
    schema_before_uninstall = collection_schema()
    config.remove_deck_and_its_extensions(ah_did)
    mids = ankihub_db.note_types_for_ankihub_deck(ah_did)
    undo_note_type_modfications(mids)
    ankihub_db.remove_deck(ah_did)
    config.set_schema_to_do_full_upload_for_once(
        new_schema_to_do_full_upload_for_once(schema_before_uninstall)
    )
    LOGGER.info(f"Uninstalled deck {ah_did}")
