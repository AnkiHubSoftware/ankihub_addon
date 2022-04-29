from anki.hooks import addHook
from aqt import gui_hooks
from aqt.editor import Editor
from PyQt6.QtCore import qDebug
from aqt.utils import chooseList, tooltip

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
    # TODO Make sure the field scheme is correct.
    #  eg, List[dict]:  [{"name": "Front", "order": 0, "value": "fun"}, {"name": "Back", "order": 1, "value": "stuff"}]
    fields = editor.note.fields
    tags = editor.note.tags
    client = AnkiHubClient()
    if command == AnkiHubCommands.CHANGE.value:
        ankihub_id = fields[0]
        response = client.create_change_note_suggestion(
            ankihub_id=ankihub_id,
            fields=fields,
            tags=tags,
        )
        if response.status_code == 201:
            tooltip("Submitted change note suggestion to AnkiHub.")
            return response.json()
    elif command == AnkiHubCommands.NEW.value:
        subscribed_decks = client._config.private_config.decks
        if len(subscribed_decks) == 1:
            deck_id = subscribed_decks[0]
        else:
            choice = chooseList(
                "Which AnKiHub deck would you like to add this note to?",
                choices=subscribed_decks,
            )
            deck_id = subscribed_decks[choice]
        response = client.create_new_note_suggestion(
            deck_id=deck_id,
            anki_id=editor.note.id,
            fields=fields,
            tags=tags,
        )
        if response.status_code == 201:
            tooltip("Submitted new note suggestion to AnkiHub.")
            return response.json()


def setup_editor_buttons(buttons, editor: Editor):
    """Add buttons to Editor."""
    # TODO Figure out how to test this
    config = Config().public_config
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


def ankihub_message_handler(handled: tuple, cmd, editor: Editor):
    """Call on_select_command when a message prefixed with 'ankihub' is received
    from the front end.
    """
    if not cmd.startswith("ankihub"):
        return handled
    _, command_value = cmd.split(":")
    on_select_command(editor, command_value)
    handled = (True, None)
    return handled


def on_select_command(editor, cmd):
    """
    Action to perform when the user selects a command from the options drop
    down menu.  This currently just sets an instance attribute on the Editor.
    """
    editor.ankihub_command = cmd
    qDebug(f"AnkiHub command set to {cmd}")


def setup():
    addHook("setupEditorButtons", setup_editor_buttons)
    gui_hooks.webview_did_receive_js_message.append(ankihub_message_handler)
    Editor.ankihub_command = AnkiHubCommands.CHANGE.value
    return Editor
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
