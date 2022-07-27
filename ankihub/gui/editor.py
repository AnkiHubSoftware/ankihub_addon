import uuid
from typing import List

import anki
import aqt
from anki.models import NoteType
from aqt import gui_hooks
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import chooseList, showText, tooltip

from .. import constants
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..config import config
from ..constants import (
    ANKI_MINOR,
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ICONS_PATH,
    AnkiHubCommands,
)
from .suggestion_dialog import SuggestionDialog


def on_ankihub_button_press(editor: Editor) -> None:
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
    ankihub_note_uuid_str = _field_vals.pop()
    if ankihub_note_uuid_str:
        ankihub_note_uuid = uuid.UUID(ankihub_note_uuid_str)
    else:
        ankihub_note_uuid = uuid.uuid4()
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
            return
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
                choices=[subscribed_decks[id]["name"] for id in subscribed_decks],
            )
            ankihub_deck_uuid = list(subscribed_decks.keys())[choice]

        if editor.addMode:

            def on_add(note: anki.notes.Note) -> None:
                response = client.create_new_note_suggestion(
                    ankihub_deck_uuid=ankihub_deck_uuid,
                    ankihub_note_uuid=ankihub_note_uuid,
                    anki_note_id=note.id,
                    fields=fields,
                    tags=tags,
                    change_type=change_type,
                    note_type_name=note.note_type()["name"],
                    anki_note_type_id=note.note_type()["id"],
                    comment=comment,
                )
                if response.status_code == 201:
                    tooltip("Submitted new note suggestion to AnkiHub.")
                gui_hooks.add_cards_did_add_note.remove(on_add)

            gui_hooks.add_cards_did_add_note.append(on_add)
            add_note_window: AddCards = editor.parentWindow  # type: ignore
            add_note_window.add_current_note()
        else:
            response = client.create_new_note_suggestion(
                ankihub_deck_uuid=ankihub_deck_uuid,
                ankihub_note_uuid=ankihub_note_uuid,
                anki_note_id=editor.note.id,
                fields=fields,
                tags=tags,
                change_type=change_type,
                note_type_name=editor.note.note_type()["name"],
                anki_note_type_id=editor.note.note_type()["id"],
                comment=comment,
            )
            if response.status_code == 201:
                tooltip("Submitted new note suggestion to AnkiHub.")


def setup_editor_buttons(buttons: List[str], editor: Editor) -> None:

    """Add buttons to Editor."""
    # TODO Figure out how to test this
    public_config = config.public_config
    hotkey = public_config["hotkey"]
    img = str(ICONS_PATH / "ankihub_button.png")
    button = editor.addButton(
        img,
        "CH",
        on_ankihub_button_press,
        tip="Send your request to AnkiHub ({})".format(hotkey),
        label='<span id="ankihub-btn-label" style="vertical-align: top;"></span>',
        id="ankihub-btn",
        keys=hotkey,
    )
    buttons.append(button)
    buttons.append(
        """<style> #ankihub-btn { width:auto; padding:1px; }
#ankihub-btn[disabled] { opacity:.4; pointer-events: none; }</style>"""
    )


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, _: aqt.editor.Editor
) -> str:
    if ANKI_MINOR >= 50:
        if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
            return js
        extra = (
            'require("svelte/internal").tick().then(() => '
            "{{ require('anki/NoteEditor').instances[0].fields["
            "require('anki/NoteEditor').instances[0].fields.length -1"
            "].element.then((element) "
            "=> {{ element.hidden = true; }}); }});"
        )
    else:
        if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
            extra = (
                "(() => {"
                'const field = document.querySelector("#fields div[data-ankihub-hidden]");'
                "if (field) {"
                "delete field.dataset.ankihubHidden;"
                "field.hidden = false;"
                "}"
                "})()"
            )
        else:
            extra = (
                "(() => {"
                'const fields = document.getElementById("fields").children;'
                "const field = fields[fields.length -1];"
                "field.dataset.ankihubHidden = true;"
                "field.hidden = true;"
                "})()"
            )
    js += extra
    return js


def refresh_ankihub_button(editor: Editor) -> None:
    """Set ankihub button label based on whether ankihub_id field is empty"""
    note = editor.note
    disable_btn_script = "document.getElementById('ankihub-btn').disabled={};"
    if ANKIHUB_NOTE_TYPE_FIELD_NAME in note:
        editor.web.eval(disable_btn_script.format("false"))
    else:
        editor.web.eval(disable_btn_script.format("true"))
        return

    set_label_script = "document.getElementById('ankihub-btn-label').textContent='{}';"
    if note[ANKIHUB_NOTE_TYPE_FIELD_NAME]:
        editor.web.eval(set_label_script.format(AnkiHubCommands.CHANGE.value))
        editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore
    else:
        editor.web.eval(set_label_script.format(AnkiHubCommands.NEW.value))
        editor.ankihub_command = AnkiHubCommands.NEW.value  # type: ignore


editor: Editor


def on_add_cards_init(add_cards: AddCards) -> None:
    global editor
    editor = add_cards.editor


def on_add_cards_change_notetype(old: NoteType, new: NoteType) -> None:
    global editor
    refresh_ankihub_button(editor)


def setup() -> None:
    gui_hooks.editor_did_init_buttons.append(setup_editor_buttons)
    gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
    gui_hooks.editor_did_load_note.append(refresh_ankihub_button)
    gui_hooks.add_cards_did_init.append(on_add_cards_init)
    gui_hooks.add_cards_did_change_note_type.append(on_add_cards_change_notetype)
    Editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
