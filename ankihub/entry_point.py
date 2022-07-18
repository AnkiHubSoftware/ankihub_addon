"""Code to be run on Anki start up."""
from aqt import gui_hooks, mw

from . import LOGGER
from .gui import browser, editor
from .gui.menu import setup_ankihub_menu
from .sync import setup_sync_on_startup


def run():
    """Call this function in __init__.py when Anki starts."""
    mw.addonManager.setWebExports(__name__, r"gui/web/.*")

    setup_sync_on_startup()

    setup_ankihub_menu()
    LOGGER.debug("Set up AnkiHub menu.")

    editor.setup()
    LOGGER.debug("Set up editor.")

    browser.setup()
    LOGGER.debug("Set up browser.")

    return mw
