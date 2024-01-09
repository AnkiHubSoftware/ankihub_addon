"""Code for setting up auto-syncing with AnkiHub on startup and/or on AnkiWeb sync.
Depends on the auto_sync setting in the public config."""
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable

from anki.hooks import wrap
from aqt import AnkiQt

from .. import LOGGER
from ..settings import ANKI_INT_VERSION, config
from .menu import AnkiHubLogin
from .operations.ankihub_sync import sync_with_ankihub
from .operations.utils import future_with_exception, future_with_result
from .threading_utils import rate_limited
from .utils import extract_argument

# Rate limit to one sync every x seconds to prevent syncs running in parallel and causing problems.
SYNC_RATE_LIMIT_SECONDS = 2


@dataclass
class _AutoSyncState:
    attempted_startup_sync = False


auto_sync_state = _AutoSyncState()


def setup_auto_sync() -> None:
    """Setup AnkiHub sync on AnkiWeb sync"""
    _setup_ankihub_sync_on_ankiweb_sync()

    # This has to be applied after _setup_ankihub_sync_on_ankiweb_sync because
    # both functions wrap AnkiQt._sync_collection_and_media and we want to rate
    # limit the outer function so that AnkiHub syncs can't be started in too
    # short time intervals.
    _rate_limit_syncing()


def _setup_ankihub_sync_on_ankiweb_sync() -> None:
    AnkiQt._sync_collection_and_media = wrap(  # type: ignore
        AnkiQt._sync_collection_and_media,
        _on_ankiweb_sync,
        "around",
    )


def _rate_limit_syncing() -> None:
    """Rate limit AnkiQt._sync_collection_and_media to avoid syncs running in parallel and causing problems.
    Syncing is not thread safe and from Sentry reports you can see that the DBErrors are raised when
    the sync is called multiple times in a short time frame (< 0.2 seconds). Not sure why this happens.
    """
    AnkiQt._sync_collection_and_media = wrap(  # type: ignore
        AnkiQt._sync_collection_and_media,
        _rate_limited,
        "around",
    )


@rate_limited(SYNC_RATE_LIMIT_SECONDS, "after_sync")
def _rate_limited(*args, **kwargs) -> None:
    """Wrapper for AnkiQt._sync_collection_and_media that is rate limited to one call every x seconds.
    The `after_sync` callable passed to the _sync_collection_and_media function is called immediately
    if the sync is rate limited."""
    _old = kwargs["_old"]
    del kwargs["_old"]

    _old(*args, **kwargs)


def _on_ankiweb_sync(*args, **kwargs) -> None:
    """Wrapper for AnkiQt._sync_collection_and_media that syncs with with AnkiHub before syncing with AnkiWeb.
    When the user is not logged into AnkiHub and the auto sync would be run otherwise, the AnkiHub login dialog
    is displayed after the AnkiWeb sync."""
    original_sync_collection_and_media = kwargs["_old"]
    del kwargs["_old"]

    args, kwargs, original_after_sync = extract_argument(
        original_sync_collection_and_media,
        args=args,
        kwargs=kwargs,
        arg_name="after_sync",
    )

    def new_after_sync() -> None:  # pragma: no cover
        # TODO: This is not being tested because in addition to it being an internal function, it is not called
        # directly within the upper level function. The upper level function instead defines another function,
        # 'sync_with_ankiweb', which calls another function where 'new_after_sync' is sent as a callback argument.
        # However, 'sync_with_ankiweb' itself will also be sent as a callback argument to '_maybe_sync_with_ankihub'
        # (see below). This means that 'new_after_sync' is effectively the callback of a callback of a function
        # that is being called here - making this a rather complex function to test.

        """The original after_sync callback function passed to AnkiQt._sync_collection_and_media is replaced by
        this function."""
        original_after_sync()

        # If we displayed the login dialog before the AnkiWeb sync, the user wouldn't be able to interact with it until
        # the sync is finished. So we display it after the AnkiWeb sync.
        if _should_auto_sync_with_ankihub() and not config.is_logged_in():
            AnkiHubLogin.display_login()

    def sync_with_ankiweb(future: Future) -> None:
        # The original function should be called even if the sync with AnkiHub fails, so we run it
        # this before future.result() (which can raise an exception)
        original_sync_collection_and_media(*args, **kwargs, after_sync=new_after_sync)

        future.result()

    try:
        _maybe_sync_with_ankihub(on_done=sync_with_ankiweb)
    except Exception as e:
        LOGGER.warning("Error while syncing with AnkiHub", exc_info=True)
        sync_with_ankiweb(future_with_exception(e))


def _maybe_sync_with_ankihub(on_done: Callable[[Future], None]) -> None:
    LOGGER.info("Running _maybe_sync_with_ankihub")

    if not config.is_logged_in():
        LOGGER.info("Not syncing with AnkiHub because user is not logged in.")
        on_done(future_with_result(None))
        return

    if _should_auto_sync_with_ankihub():
        auto_sync_state.attempted_startup_sync = True
        LOGGER.info("Syncing with AnkiHub in _new_sync_collection")
        sync_with_ankihub(on_done=on_done)
    else:
        LOGGER.info("Not syncing with AnkiHub")
        on_done(future_with_result(None))


def _workaround_for_addon_compatibility_on_startup_sync() -> None:
    # AnkiHubSync creates a backup before syncing and creating a backup requires to close
    # the collection in Anki versions lower than 2.1.50.
    # When other add-ons try to access the collection while it is closed they will get an error.
    # Many add-ons are added to the profile_did_open hook so we can wait until they are probably finished
    # and sync then.
    # Another way to deal with that is to tell users to set the auto_sync option to "never" and
    # to sync manually.
    LOGGER.info("Running _workaround_for_addon_compatibility_on_startup_sync")
    if ANKI_INT_VERSION < 50:
        sleep(3)

    LOGGER.info(
        f"Finished _workaround_for_addon_compatibility_on_startup_sync {ANKI_INT_VERSION=}"
    )

