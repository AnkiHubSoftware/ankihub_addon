import types

import aqt
from anki.decks import DeckId
from aqt.operations.deck import remove_decks

from .db import ankihub_db
from .gui.utils import ask_user
from .settings import config
from .utils import undo_note_type_modfications


def _deck_delete_hook():
    def _delete_override(self, did: DeckId) -> None:
        remove_decks(parent=self.mw, deck_ids=[did]).run_in_background()
        deck_ankihub_id = config.get_deck_uuid_by_did(did)
        if not deck_ankihub_id:
            return
        if ask_user(
            text="Would you like to also delete ankihub information of this deck?<br><br>"
            "Note: By confirming this you will also delete the information related to AnkiHub",
            title="Please confirm to proceed.",
            parent=self.mw,
        ):
            config.remove_deck(deck_ankihub_id)
            mids = ankihub_db.note_types_for_ankihub_deck(deck_ankihub_id)
            undo_note_type_modfications(mids)
            ankihub_db.remove_deck(deck_ankihub_id)

    aqt.mw.deckBrowser._delete = types.MethodType(_delete_override, aqt.mw.deckBrowser)


def setup_hooks():
    _deck_delete_hook()
