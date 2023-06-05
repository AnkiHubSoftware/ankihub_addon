"""Check if the user is subscribed to any decks that are not installed and install them if the user agrees."""
from typing import Callable, List

from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import Deck
from ...settings import config
from ..messages import messages
from ..utils import ask_user
from .deck_installation import download_and_install_decks


def check_and_install_new_deck_subscriptions(on_success: Callable[[], None]) -> None:
    """Check if there are any new deck subscriptions and install them if the user agrees.
    on_success is called when this process is finished (even if no new decks are installed)."""

    if not AnkiHubClient().is_feature_flag_enabled("new_subscription_workflow_enabled"):
        on_success()
        return

    # Check if there are any new subscriptions
    decks = _not_installed_ah_decks()
    if not decks:
        on_success()
        return

    # Ask user to confirm the installations.
    if not ask_user(
        title="AnkiHub Deck Installation",
        text=messages.deck_install_confirmation(decks),
        show_cancel_button=False,
        yes_button_label="Install",
        no_button_label="Skip",
    ):
        on_success()
        return

    # Download the new decks
    ah_dids = [deck.ankihub_deck_uuid for deck in decks]
    download_and_install_decks(ah_dids, on_success=on_success)


def _not_installed_ah_decks() -> List[Deck]:
    client = AnkiHubClient()
    remote_decks = client.get_decks_with_user_relation()
    remote_deck_ids = [deck.ankihub_deck_uuid for deck in remote_decks]

    local_deck_ids = config.deck_ids()

    not_installed_dids = set(remote_deck_ids) - set(local_deck_ids)
    result = [
        deck for deck in remote_decks if deck.ankihub_deck_uuid in not_installed_dids
    ]
    return result
