"""Code to be run on Anki start up."""
from aqt import gui_hooks, mw

from .gui import editor
from .gui.menu import setup_ankihub_menu
from .utils import hide_ankihub_field_in_editor, sync_on_profile_open
from . import LOGGER


def run():
    """Call this function in __init__.py when Anki starts."""
    gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
    gui_hooks.profile_did_open.append(sync_on_profile_open)
    mw.addonManager.setWebExports(__name__, r"gui/web/.*")
    setup_ankihub_menu()
    LOGGER.debug("Set up AnkiHub menu.")
    editor.setup()
    LOGGER.debug("Set up editor.")
    return mw
