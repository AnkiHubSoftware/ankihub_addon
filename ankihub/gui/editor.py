from pprint import pformat
from typing import Any, List, Tuple

import anki
import aqt
from anki.models import NoteType
from aqt import gui_hooks
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import showInfo, showText, tooltip

from .. import LOGGER, settings
from ..ankihub_client import AnkiHubRequestError
from ..db import ankihub_db
from ..error_reporting import report_exception_and_upload_logs
from ..settings import ANKI_MINOR, ICONS_PATH, AnkiHubCommands, config
from ..suggestions import suggest_new_note, suggest_note_update
from .suggestion_dialog import SuggestionDialog
from .utils import choose_list


def on_suggestion_button_press(editor: Editor) -> None:
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """

    try:
        on_suggestion_button_press_inner(editor)
    except AnkiHubRequestError as e:
        if "suggestion" not in e.response.url:
            raise e

        if e.response.status_code == 400:
            if non_field_errors := e.response.json().get("non_field_errors", None):
                error_message = "\n".join(non_field_errors)
            else:
                error_message = pformat(e.response.json())
                # these errors are not expected and should be reported
                report_exception_and_upload_logs(e)
            showInfo(
                text=(
                    "There are some problems with this suggestion:<br><br>"
                    f"<b>{error_message}</b>"
                ),
                title="Problem with suggestion",
            )
            LOGGER.debug(f"Can't submit suggestion due to: {pformat(error_message)}")
        elif e.response.status_code == 403:
            msg = (
                "You are not allowed to create a suggestion for this note.<br>"
                "Are you subscribed to the AnkiHub deck this notes is from?<br><br>"
                "You can only submit changes without a review if you are an owner or maintainer of the deck."
            )
            showInfo(msg, parent=editor.parentWindow)
        else:
            raise e


def on_suggestion_button_press_inner(editor: Editor) -> None:
    # The command is expected to have been set at this point already, either by
    # fetching the default or by selecting a command from the dropdown menu.
    command = editor.ankihub_command  # type: ignore
    dialog = SuggestionDialog(command)
    if not dialog.exec():
        return

    change_type, comment, auto_accept = (
        dialog.change_type(),
        dialog.comment(),
        dialog.auto_accept(),
    )
    if command == AnkiHubCommands.CHANGE.value:
        suggest_note_update(
            note=editor.note,
            change_type=change_type,
            comment=comment,
            auto_accept=auto_accept,
        )
        tooltip("Submitted change note suggestion to AnkiHub.")
        return
    elif command == AnkiHubCommands.NEW.value:
        subscribed_dids = config.deck_ids()
        if len(subscribed_dids) == 0:
            showText(
                "You aren't currently subscribed to any AnkiHub decks. "
                "Please subscribe to an AnkiHub deck first."
            )
            return
        elif len(subscribed_dids) == 1:
            ankihub_did = subscribed_dids[0]
        else:
            choice = choose_list(
                "Which AnKiHub deck would you like to add this note to?",
                choices=[config.deck_config(did).name for did in subscribed_dids],
            )
            if choice is None:
                return

            ankihub_did = subscribed_dids[choice]

        if editor.addMode:

            def on_add(note: anki.notes.Note) -> None:
                suggest_new_note(
                    note=note,
                    ankihub_deck_uuid=ankihub_did,
                    comment=comment,
                    auto_accept=auto_accept,
                )
                tooltip("Submitted new note suggestion to AnkiHub.")
                gui_hooks.add_cards_did_add_note.remove(on_add)

            gui_hooks.add_cards_did_add_note.append(on_add)
            add_note_window: AddCards = editor.parentWindow  # type: ignore
            add_note_window.add_current_note()
        else:
            suggest_new_note(
                note=editor.note,
                ankihub_deck_uuid=ankihub_did,
                comment=comment,
                auto_accept=auto_accept,
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
    if ANKI_MINOR >= 55:
        buttons.append(
            "<style> "
            "   #ankihub-btn img { "
            "     height: 100%; padding: 2px; position: relative; width: auto; top: -1px; right: 0; bottom: 0; left: 0;"
            "}\n"
            "   #ankihub-btn[disabled] { opacity:.4; pointer-events: none; }\n"
            "</style>"
        )
    else:
        buttons.append(
            "<style> "
            "    #ankihub-btn { width:auto; padding:1px; }\n"
            "    #ankihub-btn[disabled] { opacity:.4; pointer-events: none; }\n"
            "</style>"
        )


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, _: aqt.editor.Editor
) -> str:
    if ANKI_MINOR >= 55:
        if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
            return js
        extra = (
            'require("svelte/internal").tick().then(() => '
            "{{ require('anki/NoteEditor').instances[0].fields["
            "require('anki/NoteEditor').instances[0].fields.length -1"
            "].element.then((element) "
            "=> {{ element.parentElement.parentElement.hidden = true; }}); }});"
        )
    elif ANKI_MINOR >= 50:
        if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
            return js
        extra = (
            'require("svelte/internal").tick().then(() => '
            "{{ require('anki/NoteEditor').instances[0].fields["
            "require('anki/NoteEditor').instances[0].fields.length -1"
            "].element.then((element) "
            "=> {{ element.hidden = true; }}); }});"
        )
    else:
        if settings.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
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

    if ankihub_db.is_ankihub_note_type(note.mid):
        editor.web.eval(disable_btn_script.format("false"))
    else:
        editor.web.eval(disable_btn_script.format("true"))
        return

    set_label_script = "document.getElementById('ankihub-btn-label').textContent='{}';"
    if ankihub_db.ankihub_nid_for_anki_nid(note.id):
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


def setup_editor_did_load_js_message(editor: Editor) -> None:
    script = (
        "require('anki/ui').loaded.then(() => setTimeout( () => {"
        "   pycmd('editor_did_load') "
        "}));"
    )
    editor.web.eval(script)


def on_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:
    if message == "editor_did_load":
        refresh_suggestion_button(context)
        return (True, None)

    return handled


def setup() -> None:
    # setup suggestion button
    gui_hooks.editor_did_init_buttons.append(setup_editor_buttons)
    gui_hooks.editor_did_init.append(setup_editor_did_load_js_message)
    gui_hooks.webview_did_receive_js_message.append(on_js_message)
    gui_hooks.editor_did_load_note.append(refresh_suggestion_button)
    gui_hooks.add_cards_did_init.append(on_add_cards_init)
    gui_hooks.add_cards_did_change_note_type.append(on_add_cards_change_notetype)

    gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)

    Editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
