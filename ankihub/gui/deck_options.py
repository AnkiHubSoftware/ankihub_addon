from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

import aqt
from anki.decks import DeckConfigId
from aqt import qconnect
from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import deck_options_did_load, webview_did_receive_js_message
from aqt.utils import tooltip
from jinja2 import Template

from ..settings import FSRS_VERSION, config
from .js_message_handling import parse_js_message_kwargs
from .utils import active_window_or_mw, anki_theme, robust_filter

ADD_FSRS_REVERT_BUTTON_JS_PATH = (
    Path(__file__).parent / "web" / "deck_options_revert_fsrs.js"
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
        fsrs_parameters_from_deck_config = _get_fsrs_parameters(conf_id)
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


def _get_fsrs_parameters(conf_id: DeckConfigId) -> List[float]:
    """Fetch the FSRS parameters for a deck config.
    Tries version = FSRS_VERSION down to the lowest FSRS version, returns the first found list or [].
    """
    min_fsrs_version = 4  # The first version of FSRS that was used in Anki.
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    for version in range(FSRS_VERSION, min_fsrs_version - 1, -1):
        params = deck_config.get(f"fsrsParams{version}", None)
        if params is not None:
            return params

    return []


def _can_revert_from_fsrs_parameters(
    conf_id: DeckConfigId, fsrs_parameters: List[float]
) -> bool:
    """Check if we can revert from the passed FSRS parameters to the backup parameters for the provided deck config."""
    (
        fsrs_version_from_backup,
        fsrs_parameters_from_backup,
    ) = config.get_fsrs_parameters_from_backup(conf_id)
    return (
        fsrs_parameters_from_backup != fsrs_parameters
        # We can only revert if the version of the parameters in the backup is
        # less than or equal to the current version of FSRS.
        # Old Anki versions can't handle FSRS parameters from newer versions, but the other way
        # around is fine.
        and fsrs_version_from_backup <= FSRS_VERSION
    )


def _backup_fsrs_parameters(conf_id: DeckConfigId) -> bool:
    """Backup the current FSRS parameters of the specified deck-preset."""
    parameters = _get_fsrs_parameters(conf_id)

    return config.backup_fsrs_parameters(
        conf_id, version=FSRS_VERSION, parameters=parameters
    )


@robust_filter
def _on_webview_did_receive_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:

    if message.startswith(REVERT_FSRS_PARAMATERS_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]

        (
            fsrs_version_from_backup,
            fsrs_parameters_from_backup,
        ) = config.get_fsrs_parameters_from_backup(conf_id)
        if not fsrs_version_from_backup or fsrs_version_from_backup > FSRS_VERSION:
            return (True, None)

        _deck_options_dialog.dialog.web.eval(
            f"updateFsrsParametersTextarea({fsrs_parameters_from_backup})"
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
