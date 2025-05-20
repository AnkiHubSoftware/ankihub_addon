import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aqt
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
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(deck["id"])
        js = Template(ADD_FSRS_REVERT_BUTTON_JS_PATH.read_text()).render(
            {
                "THEME": anki_theme(),
                "FSRS_PARAMETERS_BACKUP_ENTRY_EXISTS": bool(
                    config.get_fsrs_parameteters_backup_entry(conf_id=conf_id)
                ),
                "ANKI_DECK_ID": deck_options_dialog._deck["id"],
            }
        )
        deck_options_dialog.web.eval(js)

    deck_options_did_load.append(_on_deck_options_did_load)

    webview_did_receive_js_message.append(_on_webview_did_receive_js_message)

    _add_backup_menu_entry()
    _add_revert_menu_entry()
    _add_print_fsrs_parameters_menu_entry()


@robust_filter
def _on_webview_did_receive_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:

    if message.startswith(REVERT_FSRS_PARAMATERS_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        _revert_to_previous_fsrs_parameters(conf_id)

        _, current_parameters = _get_live_fsrs_parameters(
            aqt.mw.col.decks.get_config(conf_id)
        )
        deck_options_dialog: DeckOptionsDialog = context
        deck_options_dialog.web.eval(
            f"updateFsrsParametersTextarea({current_parameters})"
        )

        tooltip(
            "Reverted FSRS parameters to previous snapshot.",
            parent=active_window_or_mw(),
        )
        return (True, None)
    elif message.startswith(FSRS_PARAMETERS_CHANGED_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        anki_did = kwargs["anki_deck_id"]
        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        if _backup_fsrs_parameters(conf_id):
            deck_options_dialog: DeckOptionsDialog = context
            deck_options_dialog.web.eval("revertFsrsParametersBtn.disabled = false;")

        return (True, None)

    return handled


def _get_live_fsrs_parameters(deck_config: Dict[str, Any]) -> Tuple[int, List[float]]:
    """
    Return (version, parameters) for the *highest* fsrsParamsX array present
    in this deck-config. A version is counted only if its list is non-empty.
    If FSRS is disabled entirely, returns (None, []).
    """
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


def _revert_to_previous_fsrs_parameters(conf_id: int) -> None:
    """
    Revert the specified deck-preset to the parameters stored in the “previous”
    snapshot and clear any higher-version arrays so they don't get used.
    """
    backup_entry = config.get_fsrs_parameteters_backup_entry(conf_id)
    if "previous" not in backup_entry:
        return

    deck_config = aqt.mw.col.decks.get_config(conf_id)

    # Retrieve the previous snapshot we want to restore
    previous_version: int = backup_entry["previous"]["version"]
    previous_parameters: List[float] = backup_entry["previous"]["parameters"]

    live_version, _ = _get_live_fsrs_parameters(deck_config)
    if live_version:
        # Clear all arrays belonging to a higher FSRS version
        for version in range(previous_version + 1, live_version + 1):
            field_name: str = f"fsrsParams{version}"
            deck_config[field_name] = []

    # Write the snapshot back into its native field
    target_field = f"fsrsParams{previous_version}"
    deck_config[target_field] = previous_parameters[:]

    # Persist changes to the deck config
    aqt.mw.col.decks.update_config(deck_config)

    # Clear the backup entry so it doesn't get used again
    config.clear_fsrs_parameters_backup_entry(conf_id)


def _backup_fsrs_parameters(conf_id: int) -> bool:
    """
    Backup the current FSRS parameters of the specified deck-preset.
    """
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    version, parameters = _get_live_fsrs_parameters(deck_config)

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


def _revert_current_deck_fsrs_parameters() -> None:
    """
    Revert the FSRS parameters of the currently selected deck to the previous
    snapshot.
    """
    conf_id: int = aqt.mw.col.decks.current()["conf"]
    _revert_to_previous_fsrs_parameters(conf_id)


def _add_revert_menu_entry() -> None:
    action = aqt.QAction("Revert FSRS Parameters to Previous", aqt.mw)
    action.triggered.connect(_revert_current_deck_fsrs_parameters)  # type: ignore[arg-type]
    aqt.mw.form.menuTools.addAction(action)


def _print_fsrs_parameters() -> None:
    conf_id: int = aqt.mw.col.decks.current()["conf"]
    deck_config = aqt.mw.col.decks.get_config(conf_id)
    version, parameters = _get_live_fsrs_parameters(deck_config)
    print(f"FSRS Parameters (version {version}): {parameters}")

    # Print the backup entry for debugging purposes
    backup_entry = config.get_fsrs_parameteters_backup_entry(conf_id)
    print(f"Backup Entry: {backup_entry}")


def _add_print_fsrs_parameters_menu_entry() -> None:
    action = aqt.QAction("Print FSRS Parameters", aqt.mw)
    action.triggered.connect(_print_fsrs_parameters)  # type: ignore[arg-type]
    aqt.mw.form.menuTools.addAction(action)
