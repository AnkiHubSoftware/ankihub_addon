"""Code to be run on Anki start up."""
import time
from pprint import pformat
from typing import Callable

from aqt import mw
from aqt.gui_hooks import main_window_did_init, profile_did_open, sync_did_finish

from . import LOGGER
from .addons import setup_addons
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.db_check import check_ankihub_db
from .gui.menu import setup_ankihub_menu
from .progress import setup_progress_manager
from .settings import config
from .sync import sync_with_progress


def run():
    """Call this function in __init__.py when Anki starts."""

    main_window_did_init.append(log_enabled_addons)

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    do_after_ankiweb_sync(callback=on_startup_ankiweb_sync_done)
    LOGGER.debug("Registered on_after_ankiweb_sync")

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

    return mw


def on_startup_ankiweb_sync_done():
    # Syncing with AnkiHub during sync with AnkiWeb causes an error,
    # this is why we have to wait until the AnkiWeb sync is done if there is one.
    # The database check should be done after the AnkiHub sync to not open all dialogs at once
    # and the AnkiHub sync could also make the database check obsolete.
    if config.public_config.get("sync_on_startup", True):
        sync_with_progress(on_done=check_ankihub_db)
    else:
        check_ankihub_db()


def do_after_ankiweb_sync(callback: Callable[[None], None]) -> None:
    """The callback is called after the AnkiWeb sync is done if there is one, otherwise it is called
    when the Anki profile is opened.
    """

    def on_profile_open():
        if not mw.can_auto_sync():
            callback()
        else:

            def on_sync_did_finish():
                sync_did_finish.remove(on_sync_did_finish)

                callback()

            sync_did_finish.append(on_sync_did_finish)

    profile_did_open.append(on_profile_open)


def log_enabled_addons():
    enabled_addons = [x for x in mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.debug(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))
