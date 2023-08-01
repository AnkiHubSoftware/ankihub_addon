import aqt
from anki.decks import DeckId
from anki.hooks import wrap

from ...gui.utils import ask_user
from ...main.deck_unsubscribtion import unsubscribe_from_deck
from ...settings import config


def _deck_delete_hook():
    def _delete_override(did: DeckId) -> None:
        deck_ankihub_id = config.get_deck_uuid_by_did(did)
        deck_name = aqt.mw.col.decks.name(did)
        if not deck_ankihub_id:
            return
        if ask_user(
            text="You deleted an Anki deck that was linked to an AnkiHub deck.<br>"
            "Would you like to unsubscribe from this AnkiHub deck as well?<br>"
            f"Name of the AnkiHub deck: <b>{deck_name}</b><br><br>"
            "If you have any questions about this, see "
            "<a href='https://community.ankihub.net/t/how-are-anki-decks-related-to-ankihub-decks/4811/1'>"
            "this forum topic</a> for details.",
            title="Unsubscribe from AnkiHub deck?",
            parent=aqt.mw,
        ):
            unsubscribe_from_deck(deck_ankihub_id)

    aqt.mw.deckBrowser._delete = wrap(  # type: ignore
        old=aqt.mw.deckBrowser._delete, new=_delete_override
    )


def setup():
    _deck_delete_hook()
