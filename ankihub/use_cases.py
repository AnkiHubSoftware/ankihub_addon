from .db import ankihub_db
from .main.utils import undo_note_type_modfications


def unsubscribe_from_deck(client, deck_ankihub_id, config):
    client.unsubscribe_from_deck(deck_ankihub_id)
    config.remove_deck(deck_ankihub_id)
    mids = ankihub_db.note_types_for_ankihub_deck(deck_ankihub_id)
    undo_note_type_modfications(mids)
    ankihub_db.remove_deck(deck_ankihub_id)
