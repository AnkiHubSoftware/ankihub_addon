"""Modifies the Anki editor (aqt.editor) to add AnkiHub buttons and functionality."""

import functools
from typing import Any, List, Tuple, cast

import anki
import aqt
from anki.models import NoteType
from aqt import gui_hooks
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import openLink, tooltip

from .. import settings
from ..db import ankihub_db
from ..db.models import AnkiHubNote
from ..gui.menu import AnkiHubLogin
from ..settings import (
    ANKI_INT_VERSION,
    ICONS_PATH,
    AnkiHubCommands,
    config,
    url_view_note,
    url_view_note_history,
)
from .suggestion_dialog import open_suggestion_dialog_for_single_suggestion

ANKIHUB_BTN_ID_PREFIX = "ankihub-btn"
SUGGESTION_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-suggestion"
VIEW_NOTE_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-view-note"
VIEW_NOTE_HISTORY_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-view-note-history"


def setup() -> None:
    _setup_additional_editor_buttons()
    _setup_hide_ankihub_field()


def _setup_additional_editor_buttons():
    gui_hooks.add_cards_did_init.append(_on_add_cards_init)
    gui_hooks.editor_did_init_buttons.append(_setup_editor_buttons)
    gui_hooks.editor_did_init.append(_setup_editor_did_load_js_message)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
    gui_hooks.editor_did_load_note.append(_refresh_buttons)
    gui_hooks.add_cards_did_change_note_type.append(_on_add_cards_did_change_notetype)

    Editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore


def _setup_hide_ankihub_field():
    gui_hooks.editor_will_load_note.append(_hide_ankihub_field_in_editor)


def _on_suggestion_button_press(editor: Editor) -> None:
    """
    Action to be performed when the AnkiHub icon button is clicked or when
    the hotkey is pressed.
    """

    if not config.is_logged_in():
        AnkiHubLogin.display_login()
        return

    _on_suggestion_button_press_inner(editor)


def _on_suggestion_button_press_inner(editor: Editor) -> None:
    # The command is expected to have been set at this point already, either by
    # fetching the default or by selecting a command from the dropdown menu.
    def on_did_add_note(note: anki.notes.Note) -> None:
        open_suggestion_dialog_for_single_suggestion(note, parent=editor.widget)
        gui_hooks.add_cards_did_add_note.remove(on_did_add_note)

    # If the note is not yet in the database, we need to add it first.
    # We call add_current_note() to add the note to the database,
    # and then open the suggestion dialog.
    if editor.note.id == 0:
        gui_hooks.add_cards_did_add_note.append(on_did_add_note)
        add_note_window: AddCards = editor.parentWindow  # type: ignore
        add_note_window.add_current_note()
    else:
        open_suggestion_dialog_for_single_suggestion(editor.note, parent=editor.widget)


def _setup_editor_buttons(buttons: List[str], editor: Editor) -> None:
    """Add buttons to Editor."""
    img = str(ICONS_PATH / "ankihub_button.png")
    suggestion_button = editor.addButton(
        icon=img,
        cmd=SUGGESTION_BTN_ID,
        func=lambda editor: editor.call_after_note_saved(
            functools.partial(_on_suggestion_button_press, editor), keepFocus=True
        ),
        tip=f"Send your request to AnkiHub ({_suggestion_button_hotkey()})",
        label=f'<span id="{SUGGESTION_BTN_ID}-label" style="vertical-align: top;"></span>',
        id=SUGGESTION_BTN_ID,
        keys=_suggestion_button_hotkey(),
        disables=False,
    )
    buttons.append(suggestion_button)

    view_on_ankihub_button = editor.addButton(
        icon=None,
        cmd=VIEW_NOTE_BTN_ID,
        func=_on_view_note_button_press,
        label="View on AnkiHub",
        id=VIEW_NOTE_BTN_ID,
        disables=False,
    )
    buttons.append(view_on_ankihub_button)

    view_history_on_ankihub_button = editor.addButton(
        icon=None,
        cmd=VIEW_NOTE_HISTORY_BTN_ID,
        func=_on_view_note_history_button_press,
        label="View Note History",
        id=VIEW_NOTE_HISTORY_BTN_ID,
        disables=False,
    )
    buttons.append(view_history_on_ankihub_button)

    # fix style of buttons
    if ANKI_INT_VERSION >= 55:
        buttons.append(
            "<style> "
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] img {{"
            "       height: 100%!important; padding: 2px!important; position: relative!important; "
            "       width: auto!important; top: -1px!important; right: 0!important; "
            "       bottom: 0!important; left: 0!important;"
            "       filter: invert(0)!important;"
            "   }\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'][disabled] {{ opacity:.4; }}\n"
            "</style>"
        )
    else:
        buttons.append(
            "<style> "
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] {{ width:auto; padding:1px; }}\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'][disabled] {{ opacity:.4; }}\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] img  {{filter: invert(0)!important;}}"
            "</style>"
        )


def _default_suggestion_button_tooltip() -> str:
    return f"Send your request to AnkiHub ({_suggestion_button_hotkey()})"


def _suggestion_button_hotkey():
    return config.public_config["hotkey"]


def _on_view_note_button_press(editor: Editor) -> None:
    note = editor.note
    if note is None:
        return

    if not (ankihub_nid := ankihub_db.ankihub_nid_for_anki_nid(note.id)):
        tooltip("This note has no AnkiHub id.")
        return

    url = f"{url_view_note()}{ankihub_nid}"
    openLink(url)


def _on_view_note_history_button_press(editor: Editor) -> None:
    note = editor.note
    if note is None:
        return

    if not (ankihub_nid := ankihub_db.ankihub_nid_for_anki_nid(note.id)):
        tooltip("This note has no AnkiHub id.")
        return

    ankihub_did = ankihub_db.ankihub_did_for_anki_nid(note.id)
    url = url_view_note_history().format(
        ankihub_did=ankihub_did, ankihub_nid=ankihub_nid
    )
    openLink(url)


def _hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, _: aqt.editor.Editor
) -> str:
    """Add JS to the JS code of the editor to hide the ankihub_id field if it is present."""
    hide_last_field = settings.ANKIHUB_NOTE_TYPE_FIELD_NAME in note
    if ANKI_INT_VERSION >= 50:
        refresh_fields_js = _refresh_editor_fields_for_anki_v50_and_up_js(
            hide_last_field
        )
    else:
        refresh_fields_js = _refresh_editor_fields_for_anki_below_v50_js(
            hide_last_field
        )

    result = js + refresh_fields_js
    return result


def _refresh_editor_fields_for_anki_v50_and_up_js(hide_last_field: bool) -> str:
    if ANKI_INT_VERSION >= 55:
        change_visiblility_js = """
            function changeVisibilityOfField(field_idx, visible) {
                require('anki/NoteEditor').instances[0].fields[field_idx].element.then(
                    (element) => { element.parentElement.parentElement.hidden = !visible; }
                );
            }
        """
    elif ANKI_INT_VERSION >= 50:
        change_visiblility_js = """
            function changeVisibilityOfField(field_idx, visible) {
                require('anki/NoteEditor').instances[0].fields[field_idx].element.then(
                    (element) => { element.hidden = !visible; }
                );
            }
        """
    else:
        # We don't expect this condition to occur, but adding this here to be sure we are notified if it does.
        raise RuntimeError("Function should not be called for Anki < 2.1.50")

    # This is the common part of the JS code for refreshing the fields.
    # (using an old-style format string here to avoid having to escape braces)
    refresh_fields_js = """
        setTimeout(() => {
            let hide_last_field = %s;
            let num_fields = require('anki/NoteEditor').instances[0].fields.length;

            // show all fields
            for (let i = 0; i < num_fields; i++) {
                changeVisibilityOfField(i, true);
            }

            // maybe hide last field
            if (hide_last_field) {
                changeVisibilityOfField(num_fields - 1, false);
            }
        })
        """ % (
        "true" if hide_last_field else "false"
    )

    result = change_visiblility_js + refresh_fields_js
    return result


def _refresh_editor_fields_for_anki_below_v50_js(hide_last_field: bool) -> str:
    if hide_last_field:
        return """
            (() => {
                let fields = document.getElementById("fields").children;
                // This condition is here for compatibility with the multi column editor add-on
                // https://ankiweb.net/shared/info/3491767031
                if(fields[0].nodeName == "TABLE") {
                   fields = fields[0].children;
                }
                const field = fields[fields.length -1];
                field.dataset.ankihubHidden = true;
                field.hidden = true;
            })()
            """
    else:
        return """
            (() => {
                const field = document.querySelector("#fields *[data-ankihub-hidden]");
                if (field) {
                    delete field.dataset.ankihubHidden;
                    field.hidden = false;
                }
            })()
            """


def _refresh_buttons(editor: Editor) -> None:
    """Enables/Disables buttons depending on the note type and if the note is synced with AnkiHub.
    Also changes the label of the suggestion button based on whether the note is already on AnkiHub.
    """
    note = editor.note

    # Not sure why editor or editor.web can be None here, but it happens, there are reports on sentry
    # see https://sentry.io/organizations/ankihub/issues/3788327661.
    # It probably happens when the editor is closing / loading.
    if editor is None or editor.web is None:
        return

    all_button_ids = [SUGGESTION_BTN_ID, VIEW_NOTE_BTN_ID, VIEW_NOTE_HISTORY_BTN_ID]

    # Note can also be None here. See comment above.
    if note is None or not ankihub_db.is_ankihub_note_type(note.mid):
        _disable_buttons(editor, all_button_ids)
        _set_suggestion_button_label(editor, "")
        _set_suggestion_button_tooltip(editor, "")
        return

    if ah_note := AnkiHubNote.get_or_none(anki_note_id=note.id):
        command = AnkiHubCommands.CHANGE.value

        ah_note = cast(AnkiHubNote, ah_note)
        if ah_note.was_deleted():
            _enable_buttons(editor, [VIEW_NOTE_HISTORY_BTN_ID])
            _disable_buttons(editor, [SUGGESTION_BTN_ID, VIEW_NOTE_BTN_ID])
            _set_suggestion_button_tooltip(
                editor,
                "This note has been deleted from AnkiHub. No new suggestions can be made.",
            )
        else:
            _enable_buttons(editor, all_button_ids)
            _set_suggestion_button_tooltip(
                editor,
                _default_suggestion_button_tooltip(),
            )
    else:
        command = AnkiHubCommands.NEW.value

        _enable_buttons(editor, [SUGGESTION_BTN_ID])
        _disable_buttons(editor, [VIEW_NOTE_BTN_ID, VIEW_NOTE_HISTORY_BTN_ID])
        _set_suggestion_button_tooltip(
            editor,
            _default_suggestion_button_tooltip(),
        )

    _set_suggestion_button_label(editor, command)
    editor.ankihub_command = command  # type: ignore


def _enable_buttons(editor: Editor, button_ids: List[ſtr]) -> None:
    _set_enabled_states_of_buttons(editor, button_ids, True)


def _disable_buttons(editor: Editor, button_ids: List[ſtr]) -> None:
    _set_enabled_states_of_buttons(editor, button_ids, False)


def _set_enabled_states_of_buttons(
    editor: Editor, button_ids: list[str], enabled: bool
) -> None:
    disable_btns_script = f"""
        for (const btnId of {button_ids}) {{
            document.getElementById(btnId).disabled={str(not enabled).lower()};
        }}
    """
    editor.web.eval(disable_btns_script)


def _set_suggestion_button_label(editor: Editor, label: str) -> None:
    set_label_script = (
        f"document.getElementById('{SUGGESTION_BTN_ID}-label').textContent='{{}}';"
    )
    editor.web.eval(set_label_script.format(label))


def _set_suggestion_button_tooltip(editor: Editor, text: str) -> None:
    set_tooltip_script = f"document.getElementById('{SUGGESTION_BTN_ID}').title='{{}}';"
    editor.web.eval(set_tooltip_script.format(text))


editor: Editor


def _on_add_cards_init(add_cards: AddCards) -> None:
    global editor
    editor = add_cards.editor


def _on_add_cards_did_change_notetype(old: NoteType, new: NoteType) -> None:
    global editor
    _refresh_buttons(editor)


def _setup_editor_did_load_js_message(editor: Editor) -> None:
    script = (
        "require('anki/ui').loaded.then(() => setTimeout( () => {"
        "   pycmd('editor_did_load') "
        "}));"
    )
    editor.web.eval(script)


def _on_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:
    if message == "editor_did_load":
        _refresh_buttons(context)
        return (True, None)

    return handled
