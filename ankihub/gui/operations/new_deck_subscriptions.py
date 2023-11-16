"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""
from concurrent.futures import Future
from typing import Callable, List

from ...ankihub_client import Deck
from ...settings import config
from ..messages import messages
from ..utils import ask_user
from .deck_installation import download_and_install_decks
from .utils import future_with_exception, future_with_result


def check_and_install_new_deck_subscriptions(
    subscribed_decks: List[Deck], on_done: Callable[[Future], None]
) -> None:
    """Check if there are any new deck subscriptions and install them if the user agrees."""
    try:
        # Check if there are any new subscriptions
        decks = _not_installed_ah_decks(subscribed_decks)
        if not decks:
            on_done(future_with_result(None))
            return

        # Ask user to confirm the installations.
        if not ask_user(
            title="AnkiHub | Sync",
            text=messages.deck_install_confirmation(decks),
            show_cancel_button=False,
            yes_button_label="Install",
            no_button_label="Skip",
        ):
            on_done(future_with_result(None))
            return

        # Download the new decks
        ah_dids = [deck.ah_did for deck in decks]
        download_and_install_decks(ah_dids, on_done=on_done)
    except Exception as e:
        on_done(future_with_exception(e))


def _not_installed_ah_decks(subscribed_decks: List[Deck]) -> List[Deck]:
    local_deck_ids = config.deck_ids()
    result = [deck for deck in subscribed_decks if deck.ah_did not in local_deck_ids]
    return result
