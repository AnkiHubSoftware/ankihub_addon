"""Code to be run on Anki start up."""
import time
from pprint import pformat

from aqt import mw
from aqt.gui_hooks import main_window_did_init

from . import LOGGER
from .addons import setup_addons
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.menu import setup_ankihub_menu
from .progress import setup_progress_manager
from .settings import config
from .sync import setup_sync_on_startup


def run():
    """Call this function in __init__.py when Anki starts."""

    main_window_did_init.append(log_enabled_addons)

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    if config.public_config.get("sync_on_startup", True):
        setup_sync_on_startup()
        LOGGER.debug("Set up AnkiHub sync on startup.")
    else:
        LOGGER.debug("Skipping setup sync on startup.")

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


def log_enabled_addons():
    enabled_addons = [x for x in mw.addonManager.all_addon_meta() if x.enabled]
    LOGGER.debug(f"enabled addons:\n{pformat(enabled_addons)}")


def trigger_addon_update_check():
    # This sets the last_addon_update_check time to 25 hours before now and Anki usually checks
    # for add-on updates every 24 hours, so this will trigger an add-on update check on Anki startup.
    # See https://github.com/ankitects/anki/blob/21812556a6a29c7da34561e58824219783a867e7/qt/aqt/main.py#L896-L916
    mw.pm.set_last_addon_update_check(int(time.time()) - (60 * 60 * 25))
