from concurrent.futures import Future
from typing import Callable

import aqt

from ...sync import ah_sync, show_tooltip_about_last_sync_results
from .db_check import maybe_check_databases
from .new_deck_subscriptions import check_and_install_new_deck_subscriptions


def sync_with_ankihub(on_done: Callable[[], None]) -> None:
    """Check for (and maybe install) new deck subscriptions, then download updates to decks.."""

    def on_new_deck_subscriptions_done() -> None:
        aqt.mw.taskman.with_progress(
            task=ah_sync.sync_all_decks_and_media,
            immediate=True,
            on_done=on_sync_done,
        )

    def on_sync_done(future: Future) -> None:
        future.result()

        show_tooltip_about_last_sync_results()
        maybe_check_databases()

        on_done()

    check_and_install_new_deck_subscriptions(on_success=on_new_deck_subscriptions_done)
