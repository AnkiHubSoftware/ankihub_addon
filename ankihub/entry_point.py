"""Code to be run on Anki start up."""
from aqt import mw

from . import LOGGER
from .gui import browser, editor
from .gui.menu import setup_ankihub_menu
from .sync import setup_sync_on_startup
from .config import config


def run():
    """Call this function in __init__.py when Anki starts."""

    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    if config.public_config.get("sync-on-startup", True):
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

    return mw
