import aqt
from anki.decks import DeckId
from anki.hooks import wrap

from ..main.deck_unsubscribtion import unsubscribe_from_deck_and_uninstall
from ..settings import config
from .utils import ask_user


def _deck_delete_hook():
    def _delete_override(did: DeckId) -> None:
        deck_ankihub_id = config.get_deck_uuid_by_did(did)
        deck_name = config.deck_config(deck_ankihub_id).name
        if not deck_ankihub_id:
            return
        if ask_user(
            text="You've deleted the Anki deck linked to the<br>"
            f"<b>{deck_name}</b> AnkiHub deck.<br><br>"
            "Do you also want to unsubscribe from this AnkiHub deck to avoid receiving future updates?<br><br>"
            "For more info, check out "
            "<a href='https://community.ankihub.net/t/how-are-anki-decks-related-to-ankihub-decks/4811/1'>"
            "this topic on our forum</a>.",
            title="Unsubscribe from AnkiHub Deck?",
            parent=aqt.mw,
        ):
            unsubscribe_from_deck_and_uninstall(deck_ankihub_id)

    aqt.mw.deckBrowser._delete = wrap(  # type: ignore
        old=aqt.mw.deckBrowser._delete, new=_delete_override
    )


def setup():
    _deck_delete_hook()
