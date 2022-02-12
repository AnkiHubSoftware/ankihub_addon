"""Code to be run on Anki start up."""
from aqt import gui_hooks, mw

from .gui import editor
from .gui.gui import add_ankihub_menu
from .utils import hide_ankihub_field_in_editor


def run():
    """This is the function that will be run in __init__.py when Anki starts."""
    gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
    add_ankihub_menu()
    editor.setup()
    return mw
