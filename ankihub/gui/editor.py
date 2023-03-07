from pprint import pformat
from typing import Any, List, Tuple

import anki
import aqt
from anki.models import NoteType
from aqt import gui_hooks
from aqt.addcards import AddCards
from aqt.editor import Editor
from aqt.utils import openLink, showInfo, showText, tooltip

from .. import LOGGER, settings
from ..ankihub_client import AnkiHubRequestError
from ..db import ankihub_db
from ..error_reporting import report_exception_and_upload_logs
from ..settings import (
    ANKI_MINOR,
    ICONS_PATH,
    url_view_note,
    url_view_note_history,
    AnkiHubCommands,
    config,
)
from ..suggestions import suggest_new_note, suggest_note_update
from .suggestion_dialog import SuggestionDialog
from .utils import choose_list

ANKIHUB_BTN_ID_PREFIX = "ankihub-btn"
SUGGESTION_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-suggestion"
VIEW_NOTE_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-view-note"
VIEW_NOTE_HISTORY_BTN_ID = f"{ANKIHUB_BTN_ID_PREFIX}-view-note-history"


def _on_suggestion_button_press(editor: Editor) -> None:
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
            LOGGER.info(f"Can't submit suggestion due to: {pformat(error_message)}")
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
        if suggest_note_update(
            note=editor.note,
            change_type=change_type,
            comment=comment,
            auto_accept=auto_accept,
        ):
            tooltip("Submitted change note suggestion to AnkiHub.")
        else:
            tooltip("No changes. Try syncing with AnkiHub first.")
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


def _setup_editor_buttons(buttons: List[str], editor: Editor) -> None:
    """Add buttons to Editor."""
    # TODO Figure out how to test this

    public_config = config.public_config
    hotkey = public_config["hotkey"]
    img = str(ICONS_PATH / "ankihub_button.png")
    suggestion_button = editor.addButton(
        icon=img,
        cmd=SUGGESTION_BTN_ID,
        func=_on_suggestion_button_press,
        tip=f"Send your request to AnkiHub ({hotkey})",
        label=f'<span id="{SUGGESTION_BTN_ID}-label" style="vertical-align: top;"></span>',
        id=SUGGESTION_BTN_ID,
        keys=hotkey,
    )
    buttons.append(suggestion_button)

    view_on_ankihub_button = editor.addButton(
        icon=None,
        cmd=VIEW_NOTE_BTN_ID,
        func=_on_view_note_button_press,
        label="View on AnkiHub",
        id=VIEW_NOTE_BTN_ID,
    )
    buttons.append(view_on_ankihub_button)

    view_history_on_ankihub_button = editor.addButton(
        icon=None,
        cmd=VIEW_NOTE_HISTORY_BTN_ID,
        func=_on_view_note_history_button_press,
        label="View Note History",
        id=VIEW_NOTE_HISTORY_BTN_ID,
    )
    buttons.append(view_history_on_ankihub_button)

    # fix style of buttons
    if ANKI_MINOR >= 55:
        buttons.append(
            "<style> "
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] img {{"
            "       height: 100%!important; padding: 2px!important; position: relative!important; "
            "       width: auto!important; top: -1px!important; right: 0!important; "
            "       bottom: 0!important; left: 0!important;"
            "       filter: invert(0)!important;"
            "   }\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'][disabled] {{ opacity:.4; pointer-events: none; }}\n"
            "</style>"
        )
    else:
        buttons.append(
            "<style> "
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] {{ width:auto; padding:1px; }}\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'][disabled] {{ opacity:.4; pointer-events: none; }}\n"
            f"  [id^='{ANKIHUB_BTN_ID_PREFIX}'] img  {{filter: invert(0)!important;}}"
            "</style>"
        )


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
    if ANKI_MINOR >= 50:
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
    if ANKI_MINOR >= 55:
        change_visiblility_js = """
            function changeVisibilityOfField(field_idx, visible) {
                require('anki/NoteEditor').instances[0].fields[field_idx].element.then(
                    (element) => { element.parentElement.parentElement.hidden = !visible; }
                );
            }
        """
    elif ANKI_MINOR >= 50:
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
        require("svelte/internal").tick().then(() => {
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
        });
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
    Also changes the label of the suggestion button based on whether the note is already on AnkiHub."""
    note = editor.note

    # not sure why editor or note can be None here, but it happens, there are reports on sentry
    # see https://sentry.io/organizations/ankihub/issues/3788327661
    if editor is None:
        return

    all_button_ids = [SUGGESTION_BTN_ID, VIEW_NOTE_BTN_ID, VIEW_NOTE_HISTORY_BTN_ID]

    if note is None or not ankihub_db.is_ankihub_note_type(note.mid):
        _disable_buttons(editor, all_button_ids)
        _set_suggestion_button_label(editor, "")
        return

    if ankihub_db.ankihub_nid_for_anki_nid(note.id):
        command = AnkiHubCommands.CHANGE.value
        _enable_buttons(editor, all_button_ids)
    else:
        command = AnkiHubCommands.NEW.value
        _enable_buttons(editor, [SUGGESTION_BTN_ID])
        _disable_buttons(editor, [VIEW_NOTE_BTN_ID, VIEW_NOTE_HISTORY_BTN_ID])

    _set_suggestion_button_label(editor, command)
    editor.ankihub_command = command  # type: ignore


def _enable_buttons(editor: Editor, button_ids: List[ſtr]) -> None:
    _set_enabled_states_of_buttons(editor, button_ids, True)


def _disable_buttons(editor: Editor, button_ids: List[ſtr]) -> None:
    _set_enabled_states_of_buttons(editor, button_ids, False)


def _set_enabled_states_of_buttons(
    editor: Editor, button_ids: list[str], enabled: bool
) -> None:
    if editor is None:
        return

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
    if editor is None:
        return

    editor.web.eval(set_label_script.format(label))


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


def setup() -> None:
    # setup suggestion button
    gui_hooks.editor_did_init_buttons.append(_setup_editor_buttons)
    gui_hooks.editor_did_init.append(_setup_editor_did_load_js_message)
    gui_hooks.webview_did_receive_js_message.append(_on_js_message)
    gui_hooks.editor_did_load_note.append(_refresh_buttons)
    gui_hooks.add_cards_did_init.append(_on_add_cards_init)
    gui_hooks.add_cards_did_change_note_type.append(_on_add_cards_did_change_notetype)

    gui_hooks.editor_will_load_note.append(_hide_ankihub_field_in_editor)

    Editor.ankihub_command = AnkiHubCommands.CHANGE.value  # type: ignore
    # We can wrap Editor.__init__ if more complicated logic is needed, such as
    # pulling a default command from a config option.  E.g.,
    # Editor.__init__ = wrap(Editor.__init__, init_editor)
    # See Syntax Highlighting add-on code for an example. For now, just setting
    # an instance attribute above will suffice.
