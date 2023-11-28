from concurrent.futures import Future
from typing import Callable, List

import aqt
from anki.errors import DBError

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import API_VERSION, Deck
from ...main.deck_unsubscribtion import uninstall_deck
from ...main.review_data import send_review_data
from ...settings import config
from ..deck_updater import ah_deck_updater, show_tooltip_about_last_deck_updates_results
from .db_check import maybe_check_databases
from .new_deck_subscriptions import check_and_install_new_deck_subscriptions
from .utils import future_with_exception, future_with_result


def sync_with_ankihub(on_done: Callable[[Future[None]], None]) -> None:
    """Uninstall decks the user is not subscribed to anymore, check for (and maybe install) new deck subscriptions,
    then download updates to decks."""

    def on_new_deck_subscriptions_done(
        future: Future[None], subscribed_decks: List[Deck]
    ) -> None:
        if future.exception():
            on_done(future_with_exception(future.exception()))
            return

        installed_ah_dids = config.deck_ids()
        subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
        to_sync_ah_dids = set(installed_ah_dids).intersection(set(subscribed_ah_dids))

        aqt.mw.taskman.with_progress(
            task=lambda: ah_deck_updater.update_decks_and_media(to_sync_ah_dids),
            immediate=True,
            on_done=lambda future: on_sync_done(future=future),
        )

    def on_sync_done(future: Future[None]) -> None:
        if future.exception():
            on_done(future_with_exception(future.exception()))
            return

        config.set_api_version_on_last_sync(API_VERSION)
        show_tooltip_about_last_deck_updates_results()
        maybe_check_databases()

        try:
            send_review_data()
        except DBError:  # pragma: no cover
            # TODO Attaching the ankihub db to the main db sometimes fails with a DBError
            # for some users. See:
            # https://ankihub.sentry.io/issues/4574509733/?project=6546414
            # This prevents the error from being shown to the user, until we find a fix.
            # The exception is sent to Sentry because of the LoggingIntegration.
            LOGGER.exception(
                "Could not send review data to AnkiHub"
            )  # pragma: no cover

        on_done(future_with_result(None))

    try:
        client = AnkiHubClient()
        subscribed_decks = client.get_deck_subscriptions()

        _uninstall_decks_the_user_is_not_longer_subscribed_to(
            subscribed_decks=subscribed_decks
        )
        check_and_install_new_deck_subscriptions(
            subscribed_decks=subscribed_decks,
            on_done=lambda future: on_new_deck_subscriptions_done(
                future=future, subscribed_decks=subscribed_decks
            ),
        )
    except Exception as e:
        on_done(future_with_exception(e))


def _uninstall_decks_the_user_is_not_longer_subscribed_to(
    subscribed_decks: List[Deck],
) -> None:
    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
    to_uninstall = set(installed_ah_dids).difference(subscribed_ah_dids)
    for ah_did in to_uninstall:
        uninstall_deck(ah_did)
