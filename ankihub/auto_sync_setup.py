from concurrent.futures import Future
from time import sleep
from typing import Callable, Optional

import aqt
from anki.hooks import wrap
from anki.sync import SyncAuth
from aqt.gui_hooks import sync_did_finish

from . import LOGGER
from .settings import ANKI_MINOR, config
from .sync import ah_sync, show_tooltip_about_last_sync_results
from .utils import OneTimeLock

# TODO these things should happen once per profile lifetime, for now they happen once per Anki start
# which is probably fine for now
start_up_sync_lock = OneTimeLock()
db_check_lock = OneTimeLock()

# Variable for storing the exception that was raised during the last sync with AnkiHub.
# It can't be raised directly, because the AnkiHub sync happens in the background task
# that syncs with AnkiWeb and the Anki code that calls the AnkiWeb sync should not
# have to handle AnkiHub sync exceptions.
EXCEPTION_ON_LAST_AH_SYNC: Optional[Exception] = None


def setup_ankihub_sync_on_ankiweb_sync(
    on_startup_syncs_done: Callable[[], None]
) -> None:
    # aqt.mw.col.sync_collection is called in a background task with a progress dialog.
    # This adds the AnkiHub sync to the beginning of this background task.
    aqt.mw.col.sync_collection = wrap(  # type: ignore
        aqt.mw.col.sync_collection,
        _sync_with_ankihub_and_ankiweb,
        "around",
    )

    sync_did_finish.append(
        lambda: _on_sync_did_finish(on_startup_syncs_done=on_startup_syncs_done)
    )


def maybe_sync_with_ankihub_on_startup(
    on_startup_syncs_done: Callable[[], None]
) -> None:
    if aqt.mw.can_auto_sync():
        return

    LOGGER.info(
        "Can't auto sync with AnkiWeb, so trying to sync with AnkiHub immediately."
    )

    if not start_up_sync_lock.aquire():
        LOGGER.info("Already synced with AnkiHub on startup.")
        return

    if not ah_sync.is_logged_in():
        LOGGER.info("Not syncing with AnkiHub because ah_syn.is_logged_in() is False")
        return

    _workaround_for_addon_compatibility_on_startup_sync()

    if not config.public_config["auto_sync"] in ["on_ankiweb_sync", "on_startup"]:
        LOGGER.info(
            f"Not syncing with AnkiHub because auto_sync setting is set to {config.public_config['auto_sync']}"
        )

    def on_done(future: Future):
        future.result()
        lambda: _on_sync_did_finish(on_startup_syncs_done=on_startup_syncs_done)

    LOGGER.info("Syncing with AnkiHub on startup.")
    aqt.mw.taskman.with_progress(
        task=ah_sync.sync_all_decks,
        label="Syncing with AnkiHub",
        on_done=on_done,
    )


def _on_sync_did_finish(on_startup_syncs_done: Callable[[], None]) -> None:
    if EXCEPTION_ON_LAST_AH_SYNC:
        # append the hook again, because it will be removed by Anki when the exception is raised
        sync_did_finish.append(lambda: _on_sync_did_finish(on_startup_syncs_done))
        raise EXCEPTION_ON_LAST_AH_SYNC

    show_tooltip_about_last_sync_results()

    if db_check_lock.aquire():
        on_startup_syncs_done()


def _sync_with_ankihub_and_ankiweb(auth: SyncAuth, _old: Callable) -> None:
    LOGGER.info("Running _sync_with_ankihub_and_ankiweb")

    is_startup_sync = start_up_sync_lock.aquire()

    if is_startup_sync:
        _workaround_for_addon_compatibility_on_startup_sync()

    # Anki code that runs before syncing with AnkiWeb ends the database transaction so we start a new one
    # here and end it after syncing with AnkiHub.
    aqt.mw.col.db.begin()
    global EXCEPTION_ON_LAST_AH_SYNC
    try:
        _maybe_sync_with_ankihub(is_startup_sync=is_startup_sync)
    except Exception as e:
        LOGGER.exception("Error in _maybe_sync_with_ankihub", exc_info=e)
        EXCEPTION_ON_LAST_AH_SYNC = e
    else:
        EXCEPTION_ON_LAST_AH_SYNC = None
    finally:
        # ... ending the transaction here
        aqt.mw.col.save(trx=False)

        LOGGER.info("Syncing with AnkiWeb in _sync_with_ankihub_and_ankiweb")
        result = _old(auth=auth)
        LOGGER.info("Finished syncing with AnkiWeb in _sync_with_ankihub_and_ankiweb")

        return result


def _maybe_sync_with_ankihub(is_startup_sync: bool) -> bool:
    LOGGER.info("Running _maybe_sync_with_ankihub")

    if not ah_sync.is_logged_in():
        LOGGER.info("Not syncing with AnkiHub because ah_syn.is_logged_in() is False")
        return False

    if config.public_config["auto_sync"] != "never" and (
        (config.public_config["auto_sync"] == "on_startup" and is_startup_sync)
        or config.public_config["auto_sync"] == "on_ankiweb_sync"
    ):
        LOGGER.info("Syncing with AnkiHub in _new_sync_collection")
        ah_sync.sync_all_decks()
        return True
    else:
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
