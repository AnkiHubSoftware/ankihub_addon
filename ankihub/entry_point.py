"""Code to be run on Anki start up."""
from aqt import mw

from . import LOGGER
from .gui import editor
from .gui.menu import setup_ankihub_menu
from .sync import setup_sync_on_startup


def run():
    """Call this function in __init__.py when Anki starts."""
    setup_sync_on_startup()
    setup_ankihub_menu()
    LOGGER.debug("Set up AnkiHub menu.")
    editor.setup()
    LOGGER.debug("Set up editor.")
    return mw
