import aqt
import types
from aqt.operations.deck import remove_decks
from anki.decks import DeckId
from .db import ankihub_db
from .settings import config

def deck_delete_hook():
    def _delete_override(self, did: DeckId) -> None:
        remove_decks(parent=self.mw, deck_ids=[did]).run_in_background()
        if ankihub_db.is_did_existent_on_decks(did):
            ankihub_did = ankihub_db.ankihub_did_for_did(did)
            ankihub_db.delete_deck_by_did(did)
            config.remove_deck(ankihub_did)
    
    aqt.mw.deckBrowser._delete = types.MethodType(_delete_override, aqt.mw.deckBrowser)


def setup_hooks():
    deck_delete_hook()