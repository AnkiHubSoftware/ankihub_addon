import types

import aqt
from anki.decks import DeckId
from aqt.operations.deck import remove_decks

from .db import ankihub_db
from .settings import config


def deck_delete_hook():
    def _delete_override(self, did: DeckId) -> None:
        remove_decks(parent=self.mw, deck_ids=[did]).run_in_background()
        deck_ankihub_id = config.get_deck_uuid_by_did(did)
        config.remove_deck(deck_ankihub_id)
        ankihub_db.remove_deck(deck_ankihub_id)

    aqt.mw.deckBrowser._delete = types.MethodType(_delete_override, aqt.mw.deckBrowser)


def setup_hooks():
    deck_delete_hook()
