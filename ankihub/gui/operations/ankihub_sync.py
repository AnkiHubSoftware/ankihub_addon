from concurrent.futures import Future
from typing import Callable, List

import aqt

from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import Deck
from ...main.deck_unsubscribtion import uninstall_deck
from ...settings import config
from ..sync import ah_sync, show_tooltip_about_last_sync_results
from .db_check import maybe_check_databases
from .new_deck_subscriptions import check_and_install_new_deck_subscriptions
from .utils import future_with_exception, future_with_result


def sync_with_ankihub(on_done: Callable[[Future], None]) -> None:
    """Uninstall decks the user is not subscribed to anymore, check for (and maybe install) new deck subscriptions,
    then download updates to decks."""

    def on_new_deck_subscriptions_done(future: Future) -> None:
        if future.exception():
            on_done(future_with_exception(future.exception()))
            return

        aqt.mw.taskman.with_progress(
            task=ah_sync.sync_all_decks_and_media,
            immediate=True,
            on_done=on_sync_done,
        )

    def on_sync_done(future: Future) -> None:
        if future.exception():
            on_done(future_with_exception(future.exception()))
            return

        show_tooltip_about_last_sync_results()
        maybe_check_databases()
        on_done(future_with_result(None))

    try:
        client = AnkiHubClient()
        subscribed_decks = client.get_deck_subscriptions()

        _uninstall_decks_the_user_is_not_longer_subscribed_to(
            subscribed_decks=subscribed_decks
        )
        check_and_install_new_deck_subscriptions(
            subscribed_decks=subscribed_decks, on_done=on_new_deck_subscriptions_done
        )
    except Exception as e:
        on_done(future_with_exception(e))


def _uninstall_decks_the_user_is_not_longer_subscribed_to(
    subscribed_decks: List[Deck],
) -> None:
    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ankihub_deck_uuid for deck in subscribed_decks]
    to_uninstall = set(installed_ah_dids).difference(subscribed_ah_dids)
    for ah_did in to_uninstall:
        uninstall_deck(ah_did)
