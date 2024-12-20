from concurrent.futures import Future
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Callable, List, Optional

import aqt
import aqt.sync
from anki.collection import OpChangesWithCount
from anki.hooks import wrap
from anki.sync import SyncOutput, SyncStatus
from aqt.sync import get_sync_status

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import API_VERSION, Deck
from ...main.deck_unsubscribtion import uninstall_deck
from ...main.review_data import send_daily_review_summaries, send_review_data
from ...main.utils import collection_schema
from ...settings import config, get_end_cutoff_date_for_sending_review_summaries
from ..deck_updater import ah_deck_updater, show_tooltip_about_last_deck_updates_results
from ..exceptions import FullSyncCancelled
from ..utils import sync_with_ankiweb
from .db_check import maybe_check_databases
from .new_deck_subscriptions import check_and_install_new_deck_subscriptions
from .utils import future_with_result, pass_exceptions_to_on_done


@dataclass
class _SyncState:
    schema_before_new_decks_installation: Optional[int] = None


sync_state = _SyncState()


@pass_exceptions_to_on_done
def sync_with_ankihub(on_done: Callable[[Future], None]) -> None:
    """Uninstall decks the user is not subscribed to anymore, check for (and maybe install) new deck subscriptions,
    then download updates to decks.

    If a full AnkiWeb sync is already required, sync with AnkiWeb first.
    """
    LOGGER.info("Syncing with AnkiHub...")
    config.log_private_config()

    @pass_exceptions_to_on_done
    def on_sync_status(out: SyncStatus, on_done: Callable[[Future], None]) -> None:
        if out.required == out.FULL_SYNC:
            LOGGER.info("Full sync required. Syncing with AnkiWeb first.")
            sync_with_ankiweb(partial(_after_ankiweb_sync, on_done=on_done))
        else:
            LOGGER.info("No full sync required. Syncing with AnkiHub directly.")
            _sync_with_ankihub_inner(on_done=on_done)

    get_sync_status(aqt.mw, partial(on_sync_status, on_done=on_done))


@pass_exceptions_to_on_done
def _after_ankiweb_sync(on_done: Callable[[Future], None]) -> None:
    @pass_exceptions_to_on_done
    def on_sync_status(out: SyncStatus, on_done: Callable[[Future], None]) -> None:
        if out.required == out.FULL_SYNC:
            # Stop here if user cancelled full sync
            LOGGER.info("AnkiWeb full sync cancelled.")
            raise FullSyncCancelled()

        _sync_with_ankihub_inner(on_done=on_done)

    get_sync_status(aqt.mw, partial(on_sync_status, on_done=on_done))


@pass_exceptions_to_on_done
def _sync_with_ankihub_inner(on_done: Callable[[Future], None]) -> None:
    client = AnkiHubClient()
    subscribed_decks = client.get_deck_subscriptions()

    _uninstall_decks_the_user_is_not_longer_subscribed_to(
        subscribed_decks=subscribed_decks
    )

    sync_state.schema_before_new_decks_installation = collection_schema()
    check_and_install_new_deck_subscriptions(
        subscribed_decks=subscribed_decks,
        on_done=partial(
            _on_new_deck_subscriptions_done,
            subscribed_decks=subscribed_decks,
            on_done=on_done,
        ),
    )


@pass_exceptions_to_on_done
def _on_new_deck_subscriptions_done(
    future: Future, on_done: Callable[[Future], None], subscribed_decks: List[Deck]
) -> None:
    future.result()

    if sync_state.schema_before_new_decks_installation != collection_schema():
        config.set_schema_to_do_full_upload_for_once(collection_schema())
        LOGGER.info("Full upload required after installation of decks.")
    else:
        config.set_schema_to_do_full_upload_for_once(None)
        LOGGER.info("No full upload required after installation of decks.")

    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
    to_sync_ah_dids = set(installed_ah_dids).intersection(set(subscribed_ah_dids))

    aqt.mw.taskman.with_progress(
        task=lambda: ah_deck_updater.update_decks_and_media(to_sync_ah_dids),
        immediate=True,
        on_done=partial(_on_sync_done, on_done=on_done),
    )


@pass_exceptions_to_on_done
def _on_sync_done(future: Future, on_done: Callable[[Future], None]) -> None:
    future.result()

    config.set_api_version_on_last_sync(API_VERSION)

    show_tooltip_about_last_deck_updates_results()
    maybe_check_databases()

    aqt.mw.taskman.run_in_background(
        aqt.mw.col.tags.clear_unused_tags, on_done=_on_clear_unused_tags_done
    )

    aqt.mw.taskman.run_in_background(
        send_review_data, on_done=_on_send_review_data_done
    )

    _maybe_send_daily_review_summaries()

    if config.schema_to_do_full_upload_for_once():
        # Sync with AnkiWeb to resolve the pending full upload immediately.
        # Otherwise, Anki's Sync button will be red, and clicking it will trigger a full upload.
        sync_with_ankiweb(on_done=partial(on_done, future=future_with_result(None)))
    else:
        on_done(future_with_result(None))

    LOGGER.info("Sync with AnkiHub done.")
    config.log_private_config()


def _on_clear_unused_tags_done(future: Future) -> None:
    changes: OpChangesWithCount = future.result()
    LOGGER.info("Cleared unused tags.", deleted_tags_amount=changes.count)


def _on_send_review_data_done(future: Future) -> None:
    exception = future.exception()
    if not exception:
        LOGGER.info("Review data sent successfully")
        return

    # CollectionNotOpen is raised by Anki when trying to access the collection when it is closed.
    # This happens e.g. when the sync is triggered by the user closing Anki. Then the task for sending review data
    # starts and tries to access the collection, but it is already closed. We can ignore this error.
    if "CollectionNotOpen" in str(exception):  # pragma: no cover
        LOGGER.warning(
            "Failed to send review data because the collection is closed.",
            exc_info=exception,
        )
    else:
        LOGGER.error(  # pragma: no cover
            "Failed to send review data.", exc_info=exception
        )


def _maybe_send_daily_review_summaries() -> None:
    last_sent_summary_date = config.get_last_sent_summary_date()
    if not last_sent_summary_date:
        last_sent_summary_date = (
            get_end_cutoff_date_for_sending_review_summaries() - timedelta(days=1)
        )

    feature_flags = config.get_feature_flags()
    if feature_flags.get("daily_card_review_summary", False) and (
        last_sent_summary_date < get_end_cutoff_date_for_sending_review_summaries()
    ):
        aqt.mw.taskman.run_in_background(
            lambda: send_daily_review_summaries(last_sent_summary_date),
            on_done=_on_send_daily_review_summaries_done,
        )


def _on_send_daily_review_summaries_done(future: Future) -> None:
    exception = future.exception()
    if not exception:
        config.save_last_sent_summary_date(
            get_end_cutoff_date_for_sending_review_summaries()
        )

        LOGGER.info("Daily review summaries sent successfully")
        return

    # CollectionNotOpen is raised by Anki when trying to access the collection when it is closed.
    # This happens e.g. when the sync is triggered by the user closing Anki. Then the task for sending review data
    # starts and tries to access the collection, but it is already closed. We can ignore this error.
    if "CollectionNotOpen" in str(exception):  # pragma: no cover
        LOGGER.warning(
            "Failed to send review summaries because the collection is closed.",
            exc_info=exception,
        )
    else:
        LOGGER.error(  # pragma: no cover
            "Failed to send review summaries data.", exc_info=exception
        )


def _uninstall_decks_the_user_is_not_longer_subscribed_to(
    subscribed_decks: List[Deck],
) -> None:
    installed_ah_dids = config.deck_ids()
    subscribed_ah_dids = [deck.ah_did for deck in subscribed_decks]
    to_uninstall = set(installed_ah_dids).difference(subscribed_ah_dids)
    for ah_did in to_uninstall:
        uninstall_deck(ah_did)


def _upload_if_full_sync_triggered_by_ankihub(
    mw: aqt.main.AnkiQt,
    out: SyncOutput,
    on_done: Callable[[], None],
    _old: Callable[[aqt.main.AnkiQt, SyncOutput, Callable[[], None]], None],
) -> None:
    if config.schema_to_do_full_upload_for_once() == collection_schema():
        LOGGER.info(
            "Full sync triggered by AnkiHub. Uploading changes.",
            collection_schema=collection_schema(),
        )
        if hasattr(out, "server_media_usn"):
            server_usn = out.server_media_usn if mw.pm.media_syncing_enabled() else None
            aqt.sync.full_upload(mw, server_usn, on_done)
        else:
            aqt.sync.full_upload(mw, on_done)  # type: ignore
        config.set_schema_to_do_full_upload_for_once(None)
    else:
        _old(mw, out, on_done)


def setup_full_sync_patch() -> None:
    aqt.sync.full_sync = wrap(  # type: ignore
        aqt.sync.full_sync,
        _upload_if_full_sync_triggered_by_ankihub,
        "around",
    )
