import uuid

from anki.hooks import addHook
from aqt import gui_hooks
from aqt.editor import Editor
from aqt.utils import chooseList, showText, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..config import config
from ..constants import ICONS_PATH, AnkiHubCommands
from .suggestion_dialog import SuggestionDialog


def on_ankihub_button_press(editor: Editor):
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """

    # The command is expected to have been set at this point already, either by
    # fetching the default or by selecting a command from the dropdown menu.
    command = editor.ankihub_command  # type: ignore
    dialog = SuggestionDialog(command)
    if not dialog.exec():
        return

    change_type, comment = dialog.change_type(), dialog.comment()
    # See build_note_fields in ankihub
    # _field_vals is the actual contents of each note field.
    _field_vals = list(editor.note.fields)
    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    ankihub_note_uuid = _field_vals.pop()
    if not ankihub_note_uuid:
        ankihub_note_uuid = str(uuid.uuid4())
    _fields_metadata = editor.note.note_type()["flds"][:-1]
    fields = [
        {"name": field["name"], "order": field["ord"], "value": val}
        for field, val in zip(_fields_metadata, _field_vals)
    ]
    tags = editor.note.tags
    client = AnkiHubClient()
    if command == AnkiHubCommands.CHANGE.value:
        response = client.create_change_note_suggestion(
            ankihub_note_uuid=ankihub_note_uuid,
            fields=fields,
            tags=tags,
            change_type=change_type,
            comment=comment,
        )
        if response.status_code == 201:
            tooltip("Submitted change note suggestion to AnkiHub.")
            return response.json()
    elif command == AnkiHubCommands.NEW.value:
        subscribed_decks = config.private_config.decks
        if len(subscribed_decks) == 0:
            showText(
                "You aren't currently subscribed to any AnkiHub decks. "
                "Please subscribe to an AnkiHub deck first."
            )
            return
        elif len(subscribed_decks) == 1:
            (decks,) = subscribed_decks.items()
            ankihub_deck_uuid, deck = decks
        else:
            choice = chooseList(
                "Which AnKiHub deck would you like to add this note to?",
                choices=subscribed_decks,
            )
            ankihub_deck_uuid = list(subscribed_decks.keys())[choice]

        response = client.create_new_note_suggestion(
            ankihub_deck_uuid=ankihub_deck_uuid,
            ankihub_note_uuid=ankihub_note_uuid,
            anki_id=editor.note.id,
            fields=fields,
            tags=tags,
            change_type=change_type,
            comment=comment,
        )
        if response.status_code == 201:
            tooltip("Submitted new note suggestion to AnkiHub.")
            return response.json()


def setup_editor_buttons(buttons, editor: Editor):
    """Add buttons to Editor."""
    # TODO Figure out how to test this
    public_config = config.public_config
    HOTKEY = public_config["hotkey"]
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
    options_str = select_elm.format("".join(options))
    buttons.append(options_str)
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
    LOGGER.debug(f"AnkiHub command set to {cmd}")


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
