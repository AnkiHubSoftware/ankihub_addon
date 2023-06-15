"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""
from concurrent.futures import Future
from typing import Callable, List

from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import Deck
from ...settings import config
from ..messages import messages
from ..utils import ask_user
from .deck_installation import download_and_install_decks
from .utils import future_with_exception, future_with_result


def check_and_install_new_deck_subscriptions(on_done: Callable[[Future], None]) -> None:
    """Check if there are any new deck subscriptions and install them if the user agrees."""
    try:
        # Check if there are any new subscriptions
        decks = _not_installed_ah_decks()
        if not decks:
            on_done(future_with_result(None))
            return

        # Ask user to confirm the installations.
        if not ask_user(
            title="AnkiHub Deck Installation",
            text=messages.deck_install_confirmation(decks),
            show_cancel_button=False,
            yes_button_label="Install",
            no_button_label="Skip",
        ):
            on_done(future_with_result(None))
            return

        # Download the new decks
        ah_dids = [deck.ankihub_deck_uuid for deck in decks]
        download_and_install_decks(ah_dids, on_done=on_done)
    except Exception as e:
        on_done(future_with_exception(e))


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
