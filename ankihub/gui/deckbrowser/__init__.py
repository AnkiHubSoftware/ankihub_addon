import aqt
from anki.decks import DeckId
from anki.hooks import wrap

from ...addon_ankihub_client import AddonAnkiHubClient

from ...use_cases import unsubscribe_from_deck

from ...gui.utils import ask_user
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
            f"Name of the AnkiHub deck: <b>{deck_name}</b>",
            title="Unsubscribe from AnkiHub deck?",
            parent=aqt.mw,
        ):  
            client = AddonAnkiHubClient()
            unsubscribe_from_deck(client, deck_ankihub_id, config)

    aqt.mw.deckBrowser._delete = wrap(
        old=aqt.mw.deckBrowser._delete, new=_delete_override # type: ignore
    )


def setup_deck_browser_hooks():
    _deck_delete_hook()
