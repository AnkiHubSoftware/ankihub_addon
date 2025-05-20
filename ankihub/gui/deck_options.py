from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

import aqt
from aqt import qconnect
from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import deck_options_did_load, webview_did_receive_js_message
from aqt.utils import tooltip
from jinja2 import Template

from ..main.deck_options import get_fsrs_version
from ..settings import config
from .js_message_handling import parse_js_message_kwargs
from .utils import active_window_or_mw, anki_theme, robust_filter

ADD_FSRS_REVERT_BUTTON_JS_PATH = (
    Path(__file__).parent / "web" / "add_fsrs_revert_button.js"
)
REVERT_FSRS_PARAMATERS_PYCMD = "ankihub_revert_fsrs_parameters"
FSRS_PARAMETERS_CHANGED_PYCMD = "ankihub_fsrs_parameters_changed"


@dataclass
class CurrentDeckOptionsDialog:
    """Class to store the current deck options dialog."""

    dialog: Optional[DeckOptionsDialog] = None


_deck_options_dialog = CurrentDeckOptionsDialog()


def setup() -> None:
    def _on_deck_options_did_load(deck_options_dialog: DeckOptionsDialog) -> None:
        _deck_options_dialog.dialog = deck_options_dialog

        deck = deck_options_dialog._deck
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(deck["id"])["id"]

        # Setup backup of FSRS parameters on deck options dialog close
        qconnect(deck_options_dialog.finished, lambda: _backup_fsrs_parameters(conf_id))

        # Execute JS to add the revert button
        fsrs_parameters_from_deck_config = _get_live_fsrs_parameters(conf_id)[1]
        js = Template(ADD_FSRS_REVERT_BUTTON_JS_PATH.read_text()).render(
            {
                "THEME": anki_theme(),
                "RESET_BUTTON_ENABLED_INITIALLY": _can_revert_from_fsrs_parameters(
                    conf_id, fsrs_parameters_from_deck_config
                ),
                "ANKI_DECK_ID": deck_options_dialog._deck["id"],
            }
        )
        deck_options_dialog.web.eval(js)

    deck_options_did_load.append(_on_deck_options_did_load)

    webview_did_receive_js_message.append(_on_webview_did_receive_js_message)


def _get_live_fsrs_parameters(conf_id: int) -> Tuple[int, List[float]]:
    """
    Return (version, parameters) for the fsrs parameters that are currently used by the
    specified deck-preset.
    If FSRS is enabled, returns (version, parameters).
    If FSRS is not enabled, returns (None, []).
    """
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    fsrs_version = get_fsrs_version()
    field_name = f"fsrsParams{fsrs_version}"
    parameters = deck_config.get(field_name, [])
    return fsrs_version, parameters


def _can_revert_from_fsrs_parameters(
    conf_id: int, fsrs_parameters: List[float]
) -> bool:
    fsrs_parameters_from_backup = config.get_fsrs_parameters_from_backup(conf_id)
    return (
        bool(fsrs_parameters_from_backup)
        and fsrs_parameters_from_backup != fsrs_parameters
    )


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

        _deck_options_dialog.dialog.web.eval(
            f"updateFsrsParametersTextarea({previous_parameters})"
        )

        tooltip(
            "Reverted FSRS parameters.",
            parent=active_window_or_mw(),
        )
        return (True, None)
    elif message.startswith(FSRS_PARAMETERS_CHANGED_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        fsrs_parameters_from_editor = kwargs["fsrs_parameters"]

        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        if _can_revert_from_fsrs_parameters(conf_id, fsrs_parameters_from_editor):
            _deck_options_dialog.dialog.web.eval(
                "revertFsrsParametersBtn.disabled = false;"
            )

        return (True, None)

    return handled
