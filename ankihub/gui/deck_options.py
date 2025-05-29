from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple
from uuid import UUID

import aqt
from anki.decks import DeckConfigId
from aqt import qconnect
from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import (
    deck_options_did_load,
    sync_did_finish,
    webview_did_receive_js_message,
)
from aqt.utils import tooltip
from jinja2 import Template

from .. import LOGGER
from ..main.deck_options import fsrs_parameters_equal, get_fsrs_parameters
from ..main.utils import get_deck_for_ah_did
from ..settings import (
    ANKI_INT_VERSION,
    FSRS_VERSION,
    MIN_ANKI_VERSION_FOR_FSRS_FEATURES,
    config,
)
from .js_message_handling import parse_js_message_kwargs
from .utils import active_window_or_mw, anki_theme, robust_filter

ADD_FSRS_REVERT_BUTTON_JS_PATH = (
    Path(__file__).parent / "web" / "deck_options_revert_fsrs.js"
)
REVERT_FSRS_PARAMETERS_PYCMD = "ankihub_revert_fsrs_parameters"
FSRS_PARAMETERS_CHANGED_PYCMD = "ankihub_fsrs_parameters_changed"


@dataclass
class CurrentDeckOptionsDialog:
    """Class to store the current deck options dialog."""

    dialog: Optional[DeckOptionsDialog] = None


_deck_options_dialog = CurrentDeckOptionsDialog()


def setup() -> None:
    """Set up the FSRS-parameter revert feature in the deck options dialog.

    Injects JS into the dialog, registers message handlers, and sets up a backup
    mechanism for FSRS parameters so the revert button can restore previous values."""
    if not ANKI_INT_VERSION >= MIN_ANKI_VERSION_FOR_FSRS_FEATURES:
        return

    deck_options_did_load.append(_on_deck_options_did_load)
    webview_did_receive_js_message.append(_on_webview_did_receive_js_message)

    # Syncs can update deck options, so we need to backup FSRS parameters after syncs
    sync_did_finish.append(
        lambda: _backup_fsrs_parameters_for_ah_deck(config.anking_deck_id)
    )

    # Backup FSRS parameters on startup
    _backup_fsrs_parameters_for_ah_deck(config.anking_deck_id)


def _on_deck_options_did_load(deck_options_dialog: DeckOptionsDialog) -> None:
    if not (
        config.get_feature_flags().get("fsrs_revert_button", False)
        and (conf_id := _conf_id_for_ah_deck(config.anking_deck_id))
    ):
        return

    _deck_options_dialog.dialog = deck_options_dialog

    # Setup backup of FSRS parameters on deck options dialog close
    qconnect(deck_options_dialog.finished, lambda: _backup_fsrs_parameters(conf_id))

    # Execute JS to add the revert button if FSRS is enabled
    if aqt.mw.col.get_config("fsrs"):
        js = Template(ADD_FSRS_REVERT_BUTTON_JS_PATH.read_text()).render(
            {
                "THEME": anki_theme(),
                "REVERT_FSRS_PARAMETERS_PYCMD": REVERT_FSRS_PARAMETERS_PYCMD,
                "FSRS_PARAMETERS_CHANGED_PYCMD": FSRS_PARAMETERS_CHANGED_PYCMD,
            }
        )
        deck_options_dialog.web.eval(js)


def _conf_id_for_ah_deck(ah_did: UUID) -> Optional[DeckConfigId]:
    if not (deck := get_deck_for_ah_did(ah_did)):
        return None

    return aqt.mw.col.decks.config_dict_for_deck_id(deck["id"])["id"]


def _backup_fsrs_parameters_for_ah_deck(ah_did: UUID) -> None:
    """This function is called during startup and after syncs, so we use
    broad exception handling to avoid showing error dialogs to users at these times.
    FSRS parameter backup is a convenience feature and not critical for addon functionality.
    """
    try:
        if conf_id := _conf_id_for_ah_deck(ah_did):
            _backup_fsrs_parameters(conf_id)
    except Exception as e:  # pragma: no cover
        LOGGER.exception(
            "Failed to backup FSRS parameters for AnkiHub deck",
            ah_deck_id=ah_did,
            error=str(e),
        )


def _backup_fsrs_parameters(conf_id: DeckConfigId) -> bool:
    """Backup the current FSRS parameters of the specified deck-preset."""
    version, parameters = get_fsrs_parameters(conf_id)
    return config.backup_fsrs_parameters(
        conf_id, version=version, parameters=parameters
    )


@robust_filter
def _on_webview_did_receive_js_message(
    handled: tuple[bool, Any], message: str, context: Any
) -> Tuple[bool, Any]:
    def current_conf_id() -> DeckConfigId:
        # Newer Anki versions no longer gives us the DeckOptionsDialog instance in context for some reason,
        # so we grab it from our global _deck_options_dialog instead.
        anki_did = _deck_options_dialog.dialog._deck["id"]
        return aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]

    if message == REVERT_FSRS_PARAMETERS_PYCMD:
        conf_id = current_conf_id()
        (
            fsrs_version_from_backup,
            fsrs_parameters_from_backup,
        ) = config.get_fsrs_parameters_from_backup(conf_id)
        if (
            fsrs_version_from_backup is not None
            and fsrs_version_from_backup > FSRS_VERSION
        ):
            return (True, None)

        _deck_options_dialog.dialog.web.eval(
            f"ankihubRevertFSRS.updateFsrsParametersTextarea({fsrs_parameters_from_backup})"
        )

        current_version, current_params = get_fsrs_parameters(conf_id)
        LOGGER.info(
            "fsrs_deck_preset_reverted",
            preset_name=aqt.mw.col.decks.get_config(conf_id)["name"],
            current_version=current_version,
            current_parameter_count=len(current_params),
            backup_version=fsrs_version_from_backup,
            backup_parameter_count=len(fsrs_parameters_from_backup),
        )

        tooltip(
            "Reverted FSRS parameters.",
            parent=active_window_or_mw(),
        )
        return (True, None)
    elif message.startswith(FSRS_PARAMETERS_CHANGED_PYCMD):
        kwargs = parse_js_message_kwargs(message)
        fsrs_parameters_from_editor = kwargs["fsrs_parameters"]

        conf_id = current_conf_id()
        can_revert = _can_revert_from_fsrs_parameters(
            conf_id, fsrs_parameters_from_editor
        )
        _deck_options_dialog.dialog.web.eval(
            f"ankihubRevertFSRS.revertButton.disabled = {'true' if not can_revert else 'false'};"
        )

        return (True, None)

    return handled


def _can_revert_from_fsrs_parameters(
    conf_id: DeckConfigId, fsrs_parameters: List[float]
) -> bool:
    """Check if we can revert from the passed FSRS parameters to the backup parameters for the provided deck config."""
    (
        fsrs_version_from_backup,
        fsrs_parameters_from_backup,
    ) = config.get_fsrs_parameters_from_backup(conf_id)
    return (
        # We can only revert if the version of the parameters in the backup is
        # less than or equal to the current version of FSRS, or None.
        # Old Anki versions can't handle FSRS parameters from newer versions, but the other way
        # around is fine.
        (fsrs_version_from_backup is None or fsrs_version_from_backup <= FSRS_VERSION)
        and not fsrs_parameters_equal(
            fsrs_parameters,
            fsrs_parameters_from_backup,
        )
    )
