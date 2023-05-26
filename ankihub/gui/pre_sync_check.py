from typing import Callable, List

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import Deck
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
        """
        These decks were found for installation:<br>
        """
        + "<ul>"
        + "\n".join([f"<li><b>{deck.name}</b></li>" for deck in decks])
        + """
        </ul>
        <br><br>
        Would you like to proceed with downloading and installing these decks?
        Your personal collection will be modified.<br><br>

        If you press \"Cancel\", we will just continue with deck updates.<br>

        See <a href='https://docs.ankihub.net/user_docs/.html'>https://docs.ankihub.net/user_docs/</a> for more details.
        """,
        title="AnkiHub Deck Installation",
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
