from typing import Callable, List

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import Deck
from ..messages import messages
from ..settings import config
from .decks import download_and_install_decks
from .utils import ask_user


def check_and_install_new_subscriptions(on_success: Callable[[], None]):
    """Check if there are any new subscriptions and install them if the user agrees.
    on_success is called when this process is finished (even if no new decks are installed)."""

    # Check if there are any new subscriptions
    decks = _not_installed_ah_decks()
    if not decks:
        on_success()
        return

    # Ask user to confirm the installations.
    if not ask_user(
        title="AnkiHub Deck Installation",
        text=messages.deck_install_confirmation(decks),
    ):
        on_success()
        return

    # Download the new decks
    download_and_install_decks(decks, on_success=on_success)


def _not_installed_ah_decks() -> List[Deck]:
    client = AnkiHubClient()
    remote_decks = client.get_deck_subscriptions()
    remote_deck_ids = [deck.ankihub_deck_uuid for deck in remote_decks]

    local_deck_ids = config.deck_ids()

    not_installed_dids = set(remote_deck_ids) - set(local_deck_ids)
    result = [
        deck for deck in remote_decks if deck.ankihub_deck_uuid in not_installed_dids
    ]
    return result
