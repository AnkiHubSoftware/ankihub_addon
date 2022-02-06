from ..config import config
from ..constants import ICONS_PATH
from aqt.editor import Editor


HOTKEY = config["hotkey"]


def ankihub_request():
    pass


def setup_editor_buttons(buttons, editor: Editor):
    """Add buttons to Editor."""
    # no need for a lambda since onBridgeCmd passes current editor instance
    # to method anyway (cf. "self._links[cmd](self)")
    icon_path = ICONS_PATH / "ankihub_button.png"
    button = editor.addButton(icon_path, "CH", ankihub_request,
                              tip="Send your request to AnkiHub ({})".format(HOTKEY),
                              keys=HOTKEY)
    buttons.append(button)
    return buttons
