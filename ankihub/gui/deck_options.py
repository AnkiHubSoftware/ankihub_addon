import re
from pathlib import Path
from typing import Any, List, Tuple

import aqt
from aqt import qconnect
from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import deck_options_did_load, webview_did_receive_js_message
from aqt.utils import tooltip
from jinja2 import Template

from ..settings import config
from .js_message_handling import parse_js_message_kwargs
from .utils import active_window_or_mw, anki_theme, robust_filter

ADD_FSRS_REVERT_BUTTON_JS_PATH = (
    Path(__file__).parent / "web" / "add_fsrs_revert_button.js"
)
REVERT_FSRS_PARAMATERS_PYCMD = "ankihub_revert_fsrs_parameters"
FSRS_PARAMETERS_CHANGED_PYCMD = "ankihub_fsrs_parameters_changed"


def setup() -> None:
    def _on_deck_options_did_load(deck_options_dialog: DeckOptionsDialog) -> None:
        deck = deck_options_dialog._deck
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(deck["id"])["id"]
        fsrs_parameters_changed = (
            config.get_fsrs_parameters_from_backup(conf_id)
            != _get_live_fsrs_parameters(conf_id)[1]
        )
        js = Template(ADD_FSRS_REVERT_BUTTON_JS_PATH.read_text()).render(
            {
                "THEME": anki_theme(),
                "FSRS_PARAMETERS_CHANGED": fsrs_parameters_changed,
                "ANKI_DECK_ID": deck_options_dialog._deck["id"],
            }
        )
        deck_options_dialog.web.eval(js)

        qconnect(deck_options_dialog.finished, lambda: _backup_fsrs_parameters(conf_id))

    deck_options_did_load.append(_on_deck_options_did_load)

    webview_did_receive_js_message.append(_on_webview_did_receive_js_message)

    _add_backup_menu_entry()
    _add_print_fsrs_parameters_menu_entry()


@robust_filter
def _on_webview_did_receive_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:

    if message.startswith(REVERT_FSRS_PARAMATERS_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]

        previous_parameters = config.get_fsrs_parameters_from_backup(conf_id)
        if not previous_parameters:
            return (True, None)

        deck_options_dialog: DeckOptionsDialog = context
        deck_options_dialog.web.eval(
            f"updateFsrsParametersTextarea({previous_parameters})"
        )

        tooltip(
            "Reverted FSRS parameters to previous snapshot.",
            parent=active_window_or_mw(),
        )
        return (True, None)
    elif message.startswith(FSRS_PARAMETERS_CHANGED_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        fsrs_parameters_from_editor = kwargs["fsrs_parameters"]

        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        if fsrs_parameters_from_editor != config.get_fsrs_parameters_from_backup(
            conf_id
        ):
            deck_options_dialog: DeckOptionsDialog = context
            deck_options_dialog.web.eval("revertFsrsParametersBtn.disabled = false;")

        return (True, None)

    return handled


def _get_live_fsrs_parameters(conf_id: int) -> Tuple[int, List[float]]:
    """
    Return (version, parameters) for the *highest* fsrsParamsX array present
    in this deck-config. A version is counted only if its list is non-empty.
    If FSRS is disabled entirely, returns (None, []).
    """
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    highest_present_version = max(
        [
            int(re.search(r"fsrsParams(\d+)", field).group(1))
            for field in deck_config.keys()
            if re.match(r"fsrsParams\d+", field) and deck_config[field]
        ],
        default=None,
    )
    field_name = f"fsrsParams{highest_present_version}"
    parameters = deck_config.get(field_name, [])
    return highest_present_version, parameters


def _backup_fsrs_parameters(conf_id: int) -> bool:
    """
    Backup the current FSRS parameters of the specified deck-preset.
    """
    version, parameters = _get_live_fsrs_parameters(conf_id)

    # If no FSRS parameters are present, return False
    if not version:
        return False

    # Store the current parameters in the backup entry
    return config.backup_fsrs_parameters(
        conf_id, version=version, parameters=parameters
    )


def _backup_current_deck_fsrs_parameters() -> None:
    """
    Backup the current FSRS parameters of the currently selected deck.
    """
    conf_id: int = aqt.mw.col.decks.current()["conf"]
    _backup_fsrs_parameters(conf_id)


def _add_backup_menu_entry() -> None:
    action = aqt.QAction("Backup FSRS Parameters", aqt.mw)
    action.triggered.connect(_backup_current_deck_fsrs_parameters)  # type: ignore[arg-type]
    aqt.mw.form.menuTools.addAction(action)


def _print_fsrs_parameters() -> None:
    conf_id: int = aqt.mw.col.decks.current()["conf"]
    version, parameters = _get_live_fsrs_parameters(conf_id)
    print(f"FSRS Parameters (version {version}): {parameters}")

    # Print the backup entry for debugging purposes
    backup_entry = config.get_fsrs_parameters_from_backup(conf_id)
    print(f"Backup Entry: {backup_entry}")


def _add_print_fsrs_parameters_menu_entry() -> None:
    action = aqt.QAction("Print FSRS Parameters", aqt.mw)
    action.triggered.connect(_print_fsrs_parameters)  # type: ignore[arg-type]
    aqt.mw.form.menuTools.addAction(action)
