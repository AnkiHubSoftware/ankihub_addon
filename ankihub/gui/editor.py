from anki.hooks import addHook, wrap
from aqt.editor import Editor

from ..ankihub_client import AnkiHubClient
from ..config import Config
from ..constants import ICONS_PATH, AnkiHubCommands


def on_ankihub_button_press(editor: Editor):
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """
    # The command is expected to have been set at this point already, either by
    # fetching the default or by selecting a command from the dropdown menu.
    command = editor.ankihub_command
    # Get the current Note ID for passing into the request below.
    note_id = editor.note.id
    client = AnkiHubClient()
    if command == AnkiHubCommands.CHANGE.value:
        response = client.submit_change()
    elif command == AnkiHubCommands.NEW.value:
        response = client.submit_new_note()
    return response


def setup_editor_buttons(buttons, editor: Editor):
    """Add buttons to Editor."""
    # TODO Figure out how to test this
    config = Config().config
    HOTKEY = config["hotkey"]
    img = str(ICONS_PATH / "ankihub_button.png")
    button = editor.addButton(
        img,
        "CH",
        on_ankihub_button_press,
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
    for cmd in AnkiHubCommands:
        options.append(f"<option>{cmd.value}</option>")
    options = select_elm.format("".join(options))
    buttons.append(options)
    return buttons


def on_bridge_command(editor: Editor, cmd, _old):
    if not cmd.startswith("ankihub"):
        return _old(editor, cmd)
    _, command_value = cmd.split(":")
    on_select_command(editor, command_value)


def on_select_command(editor, cmd):
    """
    Action to perform when the user selects a command from the options drop
    down menu.  This currently just sets an instance attribute on the Editor.
    """
    editor.ankihub_command = cmd


def setup():
    addHook("setupEditorButtons", setup_editor_buttons)
    Editor.onBridgeCmd = wrap(Editor.onBridgeCmd, on_bridge_command, "around")
    Editor.ankihub_command = AnkiHubCommands.CHANGE.value
    return Editor
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
