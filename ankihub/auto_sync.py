"""Code for setting up auto-syncing with AnkiHub on startup and/or on AnkiWeb sync.
Depends on the auto_sync setting in the public config."""
from dataclasses import dataclass
from time import sleep
from typing import Callable, Optional

import aqt
from anki.collection import Collection
from anki.hooks import wrap
from aqt import AnkiQt
from aqt.gui_hooks import profile_did_open, profile_will_close, sync_did_finish

from . import LOGGER
from .gui.db_check import maybe_check_databases
from .gui.pre_sync_check import check_and_install_new_subscriptions
from .settings import ANKI_MINOR, config
from .sync import ah_sync, show_tooltip_about_last_sync_results
from .threading_utils import rate_limited


@dataclass
class _AutoSyncState:
    attempted_startup_sync = False
    synced_with_ankihub_on_last_ankiweb_sync = False
    exception_on_last_ah_sync: Optional[Exception] = None
    profile_is_closing = False


auto_sync_state = _AutoSyncState()


def setup_ankihub_sync_on_ankiweb_sync() -> None:
    # aqt.mw.col.sync_collection is called in a background task with a progress dialog.
    # This adds the AnkiHub sync to the beginning of this background task.
    Collection.sync_collection = wrap(  # type: ignore
        Collection.sync_collection,
        _sync_with_ankihub_and_ankiweb,
        "around",
    )

    sync_did_finish.append(_on_sync_did_finish)

    _setup_profile_state_hooks()

    _rate_limit_syncincg()


def _setup_profile_state_hooks() -> None:
    profile_will_close.append(_on_profile_will_close)
    profile_did_open.append(_on_profile_did_open)


def _on_profile_will_close() -> None:
    auto_sync_state.profile_is_closing = True


def _on_profile_did_open() -> None:
    auto_sync_state.profile_is_closing = False


def _rate_limit_syncincg() -> None:
    """Rate limit AnkiQt._sync_collection_and_media to avoid
    "Cannot start transaction within a transaction" DBErrors.
    Syncing is not thread safe and from Sentry reports you can see that the DBErrors are raised when
    the sync is called multiple times in a short time frame (< 0.2 seconds). Not sure why this happens.
    """
    AnkiQt._sync_collection_and_media = wrap(  # type: ignore
        AnkiQt._sync_collection_and_media,
        _rate_limited,
        "around",
    )


@rate_limited(2, "after_sync")
def _rate_limited(*args, **kwargs) -> None:
    """Wrapper for AnkiQt._sync_collection_and_media that is rate limited to one call every x seconds.
    The `after_sync` callable passed to the _sync_collection_and_media function is called immediately
    if the sync is rate limited."""
    _old = kwargs["_old"]
    del kwargs["_old"]

    _old(*args, **kwargs)


def _on_sync_did_finish() -> None:
    """Called from the main thread when the sync is finished."""
    if auto_sync_state.exception_on_last_ah_sync:
        # If the profile is getting closed, we don't want to raise the exception, because it would
        # disrupt the profile closing process.
        if auto_sync_state.profile_is_closing:
            return

        # append the hook again, because it will be removed by Anki when the exception is raised
        sync_did_finish.append(_on_sync_did_finish)
        raise auto_sync_state.exception_on_last_ah_sync

    if auto_sync_state.synced_with_ankihub_on_last_ankiweb_sync:
        show_tooltip_about_last_sync_results()
        check_and_install_new_subscriptions(on_success=lambda: None)

    if not auto_sync_state.profile_is_closing:
        maybe_check_databases()


def _sync_with_ankihub_and_ankiweb(*args, **kwargs) -> None:
    LOGGER.info("Running _sync_with_ankihub_and_ankiweb")

    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    is_startup_sync = not auto_sync_state.attempted_startup_sync
    auto_sync_state.attempted_startup_sync = True

    if is_startup_sync:
        _workaround_for_addon_compatibility_on_startup_sync()

    # Anki code that runs before syncing with AnkiWeb ends the database transaction so we start a new one
    # here and end it after syncing with AnkiHub.
    aqt.mw.col.db.begin()
    try:
        _maybe_sync_with_ankihub(is_startup_sync=is_startup_sync)
    except Exception as e:
        LOGGER.warning("Error in _maybe_sync_with_ankihub")
        auto_sync_state.exception_on_last_ah_sync = e
    else:
        auto_sync_state.exception_on_last_ah_sync = None
    finally:
        # ... ending the transaction here
        aqt.mw.col.save(trx=False)

        LOGGER.info("Syncing with AnkiWeb in _sync_with_ankihub_and_ankiweb")
        result = _old(*args, **kwargs)
        LOGGER.info("Finished syncing with AnkiWeb in _sync_with_ankihub_and_ankiweb")

        return result


def _maybe_sync_with_ankihub(is_startup_sync: bool) -> bool:
    LOGGER.info("Running _maybe_sync_with_ankihub")

    if not config.is_logged_in():
        LOGGER.info("Not syncing with AnkiHub because user is not logged in.")
        return False

    if config.public_config["auto_sync"] != "never" and (
        (config.public_config["auto_sync"] == "on_startup" and is_startup_sync)
        or config.public_config["auto_sync"] == "on_ankiweb_sync"
    ):
        LOGGER.info("Syncing with AnkiHub in _new_sync_collection")
        ah_sync.sync_all_decks_and_media()
        auto_sync_state.synced_with_ankihub_on_last_ankiweb_sync = True
        return True
    else:
        auto_sync_state.synced_with_ankihub_on_last_ankiweb_sync = False
        LOGGER.info("Not syncing with AnkiHub")
        return False


def _workaround_for_addon_compatibility_on_startup_sync() -> None:
    # AnkiHubSync creates a backup before syncing and creating a backup requires to close
    # the collection in Anki versions lower than 2.1.50.
    # When other add-ons try to access the collection while it is closed they will get an error.
    # Many add-ons are added to the profile_did_open hook so we can wait until they are probably finished
    # and sync then.
    # Another way to deal with that is to tell users to set the auto_sync option to "never" and
    # to sync manually.
    LOGGER.info("Running _workaround_for_addon_compatibility_on_startup_sync")
    if ANKI_MINOR < 50:
        sleep(3)

    LOGGER.info(
        f"Finished _workaround_for_addon_compatibility_on_startup_sync {ANKI_MINOR=}"
    )
