"""Code to be run on Anki start up."""
import time
from pprint import pformat
from typing import Callable

from anki.errors import CardTypeError
from aqt import mw
from aqt.gui_hooks import profile_did_open, sync_did_finish

from . import LOGGER
from .addons import setup_addons
from .db import ankihub_db
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.anki_db_check import check_anki_db
from .gui.db_check import check_ankihub_db
from .gui.menu import refresh_ankihub_menu, setup_ankihub_menu
from .progress import setup_progress_manager
from .settings import ANKI_VERSION, config, setup_profile_data_folder
from .sync import sync_with_progress
from .utils import modify_note_type_templates

# some code needs to be run only once even if the Anki profile changes
ATTEMPTED_GENERAL_SETUP_BEFORE = False


def run():
    """Call this function in __init__.py when Anki starts."""
    profile_did_open.append(on_profile_did_open)


def on_profile_did_open():
    if not profile_setup():
        return

    after_profile_setup()

    global ATTEMPTED_GENERAL_SETUP_BEFORE
    if ATTEMPTED_GENERAL_SETUP_BEFORE:
        return

    # The variable is set to True here and not at the end of the function because
    # the non_profile_setup should not be run if it has failed before.
    # If it were to run again, there could be duplicated menus and other unwanted side effects.
    ATTEMPTED_GENERAL_SETUP_BEFORE = True

    general_setup()


def profile_setup() -> bool:
    """Set up profile data folder, config, and AnkiHub DB for the current profile.
    Returns whether the profile setup was successful.
    """
    if not setup_profile_data_folder():
        return False
    LOGGER.debug(f"Set up profile data folder for the current profile: {mw.pm.name}")

    config.setup()
    LOGGER.debug("Setup config for the current profile.")

    ankihub_db.setup_and_migrate()
    LOGGER.debug("Set up and migrated AnkiHub DB for the current profile.")

    from .gui.menu import ankihub_menu

    if ankihub_menu:
        refresh_ankihub_menu()
        LOGGER.debug("Refreshed AnkiHub menu.")

    return True


def after_profile_setup():
    log_enabled_addons()

    # This adjusts note type templates of note types used by AnkiHub notes when the profile is opened.
    # If this wouldn't be called here the templates would only be adjusted when syncing with AnkiHub.
    # We want the modifications to be present even if the user doesn't sync with AnkiHub, so we call
    # this here.
    adjust_ankihub_note_type_templates()


def general_setup():
    """Set up things that don't depend on the profile and should only be run once, even if the
    profile changes."""

    LOGGER.debug(f"{ANKI_VERSION=}")

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    maybe_do_or_setup_ankihub_sync(after_startup_syncs=on_startup_syncs_done)
    LOGGER.debug("Did or set up ankihub sync.")

    setup_ankihub_menu()
    LOGGER.debug("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.debug("Set up editor.")

    browser.setup()
    LOGGER.debug("Set up browser.")

    setup_addons()
    LOGGER.debug("Set up addons.")

    setup_error_handler()
    LOGGER.debug("Set up error handler.")

    setup_progress_manager()
    LOGGER.debug("Set up progress manager.")

    trigger_addon_update_check()
    LOGGER.debug("Triggered add-on update check.")

    from . import media_export  # noqa: F401

    LOGGER.debug("Loaded media_export.")


def on_startup_syncs_done() -> None:
    # Called after AnkiWeb sync and AnkiHub sync are done after starting Anki.
    check_ankihub_db(on_success=check_anki_db)


def maybe_do_or_setup_ankihub_sync(after_startup_syncs: Callable[[], None]):
    # The AnkiHub sync can't happen during the AnkiWeb sync as this would cause errors.
    # So the approach taken here is to call AnkiHub sync after AnkiWeb sync is done.
    # It is possible for the AnkiWeb sync to be disabled / not possible, in this case
    # the AnkiHub sync is done immediately (if it is enabled).
    # after_startup_syncs is called after the startup syncs, or immediately if the syncs are not done.
    LOGGER.info(
        "Maybe do or set up AnkiHub sync. "
        f"sync_on_startup={config.public_config['sync_on_startup']} "
        f"sync_on_ankiweb_sync={config.public_config['sync_on_ankiweb_sync']} "
    )

    if config.public_config["sync_on_ankiweb_sync"]:
        # for the first time after opening Anki, AnkiWeb sync is done, then AnkiHub sync, then after_startup_syncs
        # (assuming AnkiWeb sync is possible, otherwise AnkiHub sync is done and then after_startup_syncs)
        # for subsequent AnkiWeb syncs, AnkiHub sync is done

        def wrapper():
            LOGGER.info(
                f"maybe_do_or_setup_ankihub_sync.wrapper was called with {wrapper.is_startup_sync=}"  # type: ignore
            )
            if wrapper.is_startup_sync:  # type: ignore
                wrapper.is_startup_sync = False  # type: ignore
                sync_with_progress(on_done=after_startup_syncs)
            else:
                sync_with_progress()

        wrapper.is_startup_sync = True  # type: ignore

        sync_did_finish.append(wrapper)

        if not mw.can_auto_sync() and config.public_config["sync_on_startup"]:
            LOGGER.info("AnkiWeb sync is not possible, so AnkiHub sync is done now.")
            wrapper.is_startup_sync = False  # type: ignore
            sync_with_progress(after_startup_syncs)

    elif config.public_config["sync_on_startup"]:
        # first AnkiWeb sync is attempted, then AnkiHub sync, then after_startup_syncs
        _call_once_after_ankiweb_sync_or_now_if_cant_sync(
            lambda: sync_with_progress(after_startup_syncs)
        )
    else:
        # there is no ankihub sync on startup, so it is enough to call after_startup_sync after the AnkiWeb sync
        _call_once_after_ankiweb_sync_or_now_if_cant_sync(after_startup_syncs)


def _call_once_after_ankiweb_sync_or_now_if_cant_sync(
    callback: Callable[[], None]
) -> None:
    """Call the callback once after the AnkiWeb sync is done if there will be a sync, otherwise call it
    immediately."""

    def wrapper():
        sync_did_finish.remove(wrapper)
        callback()

    if mw.can_auto_sync():
        sync_did_finish.append(wrapper)
    else:
        LOGGER.info("AnkiWeb sync is not possible, so callback is called now.")
        callback()


def log_enabled_addons():
    enabled_addons = [x for x in mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.debug(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))


def adjust_ankihub_note_type_templates():
    mids = ankihub_db.ankihub_note_type_ids()

    # Filter out note types that don't exist in the Anki database to avoid errors.
    mids_filtered = [mid for mid in mids if mw.col.models.get(mid)]

    # we don't want the setup to fail if there is a problem with the note type templates
    # the CardTypeError can happen when the template has a problem (for example a missing field)
    try:
        modify_note_type_templates(mids_filtered)
    except CardTypeError:
        LOGGER.exception("Failed to adjust AnkiHub note type templates.")
