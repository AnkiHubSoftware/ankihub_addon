import uuid
from pprint import pformat
from typing import List

import anki
import aqt
from anki.models import NoteType
from aqt import gui_hooks
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import chooseList, showInfo, showText, tooltip

from .. import LOGGER, constants
from ..ankihub_client import AnkiHubRequestError
from ..settings import config
from ..constants import (
    ANKI_MINOR,
    ANKIHUB_NOTE_TYPE_FIELD_NAME,
    ICONS_PATH,
    AnkiHubCommands,
)
from ..suggestions import suggest_new_note, suggest_note_update
from .suggestion_dialog import SuggestionDialog


def on_suggestion_button_press(editor: Editor) -> None:
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """

    try:
        on_suggestion_button_press_inner(editor)
    except AnkiHubRequestError as e:
        if "suggestion" in e.response.url and e.response.status_code == 400:
            error_messages = e.response.json()["non_field_errors"]
            newline = "\n"  # fstring expression parts can't contain backslashes
            showInfo(
                text=(
                    "There are some problems with this suggestion:<br><br>"
                    f"<b>{newline.join(error_messages)}</b>"
                ),
                title="Problem with suggestion",
            )
            LOGGER.debug(f"Can't submit suggestion due to: {pformat(error_messages)}")
            return
        raise e


def on_suggestion_button_press_inner(editor: Editor) -> None:
    # The command is expected to have been set at this point already, either by
    # fetching the default or by selecting a command from the dropdown menu.
    command = editor.ankihub_command  # type: ignore
    dialog = SuggestionDialog(command)
    if not dialog.exec():
        return

    change_type, comment = dialog.change_type(), dialog.comment()
    if command == AnkiHubCommands.CHANGE.value:
        suggest_note_update(
            note=editor.note,
            change_type=change_type,
            comment=comment,
        )
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
            ankihub_did, _ = decks
        else:
            choice = chooseList(
                "Which AnKiHub deck would you like to add this note to?",
                choices=[subscribed_decks[id]["name"] for id in subscribed_decks],
            )
            ankihub_did = list(subscribed_decks.keys())[choice]

        if editor.addMode:

            def on_add(note: anki.notes.Note) -> None:
                suggest_new_note(
                    note=note,
                    ankihub_deck_uuid=uuid.UUID(ankihub_did),
                    comment=comment,
                )
                tooltip("Submitted new note suggestion to AnkiHub.")
                gui_hooks.add_cards_did_add_note.remove(on_add)

            gui_hooks.add_cards_did_add_note.append(on_add)
            add_note_window: AddCards = editor.parentWindow  # type: ignore
            add_note_window.add_current_note()
        else:
            suggest_new_note(
                note=editor.note,
                ankihub_deck_uuid=uuid.UUID(ankihub_did),
                comment=comment,
            )
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
        on_suggestion_button_press,
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
                'const field = document.querySelector("#fields *[data-ankihub-hidden]");'
                "if (field) {"
                "delete field.dataset.ankihubHidden;"
                "field.hidden = false;"
                "}"
                "})()"
            )
        else:
            extra = (
                "(() => {"
                'let fields = document.getElementById("fields").children;'
                # For compatibility with the multi column editor add-on https://ankiweb.net/shared/info/3491767031
                'if(fields[0].nodeName == "TABLE") {'
                "   fields = fields[0].children;"
                "}"
                "const field = fields[fields.length -1];"
                "field.dataset.ankihubHidden = true;"
                "field.hidden = true;"
                "})()"
            )
    js += extra
    return js


def refresh_suggestion_button(editor: Editor) -> None:
    """Set ankihub button label based on whether ankihub_id field is empty"""
    note = editor.note
    disable_btn_script = "document.getElementById('ankihub-btn').disabled={};"

    if note is None:
        # there were error reports where note was None, maybe because of some other add-on
        editor.web.eval(disable_btn_script.format("true"))
        return

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
    refresh_suggestion_button(editor)


def setup() -> None:
    gui_hooks.editor_did_init_buttons.append(setup_editor_buttons)
    gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
    gui_hooks.editor_did_load_note.append(refresh_suggestion_button)
    gui_hooks.add_cards_did_init.append(on_add_cards_init)
    gui_hooks.add_cards_did_change_note_type.append(on_add_cards_change_notetype)
    Editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
