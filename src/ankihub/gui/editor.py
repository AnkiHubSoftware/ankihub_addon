from anki.hooks import addHook, wrap

from ..config import Config
from ..constants import ICONS_PATH, CommandList
from aqt.editor import Editor


config = Config().config
HOTKEY = config["hotkey"]


def ankihub_request(editor):
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """
    pass


def setup_editor_buttons(buttons, editor: Editor):
    """Add buttons to Editor."""
    img = str(ICONS_PATH / "ankihub_button.png")
    button = editor.addButton(
        img,
        "CH",
        ankihub_request,
        tip="Send your request to AnkiHub ({})".format(HOTKEY),
        keys=HOTKEY,
    )
    buttons.append(button)

    options = []
    select_elm = (
        "<select "
        """onchange='pycmd("ankihub:" + this.selectedOptions[0].text)'"""
        "style='vertical-align: top;'>"
        "{}"
        "</select>"
    )
    for cmd in CommandList:
        options.append(f"<option>{cmd.value}</option>")
    options = select_elm.format("".join(options))
    buttons.append(options)
    return buttons


def on_bridge_command(ed, cmd, _old):
    print(cmd)
    if not cmd.startswith("ankihub"):
        return _old(ed, cmd)
    (type, cmd) = cmd.split(":")
    on_select_command(ed, cmd)


def on_select_command(editor, cmd):
    """
    Action to perform when the user selects a command from the options drop
    down menu.
    """
    pass


def setup():
    addHook("setupEditorButtons", setup_editor_buttons)
    Editor.onBridgeCmd = wrap(Editor.onBridgeCmd, on_bridge_command, "around")
    # Editor.__init__ = wrap(Editor.__init__, init_highlighter)
