"""Code to be run on Anki start up."""
from aqt import mw

from . import LOGGER
from .addons import setup_addons
from .config import config
from .errors import setup_error_handler
from .gui import browser, editor
from .gui.menu import setup_ankihub_menu
from .progress import setup_progress_manager
from .sync import setup_sync_on_startup


def run():
    """Call this function in __init__.py when Anki starts."""

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
    LOGGER.debug("Set up progress manager")

    return mw
