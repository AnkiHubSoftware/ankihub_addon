from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import aqt
from anki import scheduler_pb2
from anki.decks import DeckConfigDict, DeckConfigId
from aqt import qconnect
from aqt.deckoptions import DeckOptionsDialog
from aqt.gui_hooks import deck_options_did_load, webview_did_receive_js_message
from aqt.qt import QCheckBox, QDialogButtonBox
from aqt.utils import tooltip
from jinja2 import Template

from .. import LOGGER
from ..main.deck_options import get_fsrs_parameters
from ..settings import ANKI_INT_VERSION, FSRS_VERSION, config
from .js_message_handling import parse_js_message_kwargs
from .utils import active_window_or_mw, anki_theme, robust_filter, show_dialog

# Minimum Anki version for which the AnkiHub add-on provides FSRS features
MIN_ANKI_VERSION_FOR_FSRS_FEATURES = 241100

FSRS_OPTIMIZATION_REMINDER_INTERVAL_DAYS = 30

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
        _, fsrs_parameters_from_deck_config = get_fsrs_parameters(conf_id)
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
    version, parameters = get_fsrs_parameters(conf_id)
    if not version:
        return False

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


def maybe_show_fsrs_optimization_reminder() -> None:
    if not config.get_feature_flags().get("fsrs_reminder", False):
        return

    deck_config = config.deck_config(config.anking_deck_id)
    if not deck_config:
        return

    anki_did = deck_config.anki_id
    deck_configs_for_update = aqt.mw.col.decks.get_deck_configs_for_update(anki_did)
    if (
        config.public_config["remind_to_optimize_fsrs_parameters"]
        and ANKI_INT_VERSION >= MIN_ANKI_VERSION_FOR_FSRS_FEATURES
        and deck_configs_for_update.fsrs
        # days_since_last_fsrs_optimize is a global value, not just for the current deck, but that's okay, because
        # if the user optimized the parameters for some deck, they probably don't need the reminder
        and deck_configs_for_update.days_since_last_fsrs_optimize
        >= FSRS_OPTIMIZATION_REMINDER_INTERVAL_DAYS
    ):
        _show_fsrs_optimization_reminder()


def _show_fsrs_optimization_reminder() -> None:
    deck_config = config.deck_config(config.anking_deck_id)
    if not deck_config:
        return

    anki_did = deck_config.anki_id
    if aqt.mw.col.decks.get(anki_did) is None:
        return

    def on_button_clicked(button_idx: Optional[int]) -> None:
        optimize = button_idx == 1

        assert isinstance(dialog.dont_show_this_again_cb, QCheckBox)
        dont_show_again = dialog.dont_show_this_again_cb.isChecked()

        LOGGER.info(
            "fsrs_optimization_reminder_dialog_choice",
            user_choice="optimize" if optimize else "skip",
            dont_show_again=dont_show_again,
        )

        if dont_show_again:
            config.public_config["remind_to_optimize_fsrs_parameters"] = False
            config.save_public_config()

        if not optimize:
            return

        if aqt.mw.col.decks.get(anki_did) is None:
            return

        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        optimize_fsrs_parameters(conf_id)

    dialog = show_dialog(
        text="""
            <h3>🛠️ Keep Your FSRS Scheduler Optimized</h3>
            <p>To keep your reviews efficient, AnKing recommends optimizing your
            <a href="https://docs.ankiweb.net/deck-options.html#fsrs">FSRS</a>
            (Free Spaced Repetition Scheduler) parameters monthly.</p>
            <p>You can always undo changes by clicking “Revert to Previous Parameters” in the deck settings.</p>
            <p>⚠️ <strong>Prevent data loss:</strong> make sure all your other devices are synced
            with AnkiWeb before proceeding.
            </p>
            <p><strong>Would you like us to optimize the AnKing FSRS parameters for you?</strong></p>
            """,
        title="AnKing Recommendation",
        buttons=[
            ("Skip", QDialogButtonBox.ButtonRole.RejectRole),
            ("Optimize", QDialogButtonBox.ButtonRole.AcceptRole),
        ],
        default_button_idx=1,
        callback=on_button_clicked,
        open_dialog=False,
        add_title_to_body_on_mac=False,
        parent=aqt.mw,
    )

    dialog.dont_show_this_again_cb = QCheckBox("Don't show this again")
    layout = dialog.content_layout
    layout.insertWidget(
        layout.count() - 2,
        dialog.dont_show_this_again_cb,
    )
    dialog.adjustSize()

    dialog.show()


def optimize_fsrs_parameters(
    conf_id: DeckConfigId, on_done: Optional[Callable[[], None]] = None
) -> None:
    deck_config = aqt.mw.col.decks.get_config(conf_id)

    _, fsrs_parameters = get_fsrs_parameters(conf_id)

    def compute_fsrs_params() -> scheduler_pb2.ComputeFsrsParamsResponse:
        deck_config_name_escaped = (
            deck_config["name"].replace("\\", "\\\\").replace('"', '\\"')
        )
        default_search = f'preset:"{deck_config_name_escaped}" -is:suspended'

        ignore_revlog_before_date_str = (
            deck_config.get("ignoreRevlogsBeforeDate") or "1970-01-01"
        )
        ignore_revlog_before_date = datetime.fromisoformat(
            ignore_revlog_before_date_str
        ).replace(tzinfo=timezone.utc)
        ignore_revlog_before_ms = int(ignore_revlog_before_date.timestamp() * 1000)

        extra_kwargs = {}
        if ANKI_INT_VERSION >= 250200:
            # The num_of_relearning_steps parameter became available in Anki 25.02
            extra_kwargs[
                "num_of_relearning_steps"
            ] = _get_amount_relearning_steps_in_day(
                deck_config,
            )

        return aqt.mw.col.backend.compute_fsrs_params(
            search=deck_config.get(
                "paramSearch", deck_config.get("weightSearch", default_search)
            ),
            current_params=fsrs_parameters,
            ignore_revlogs_before_ms=ignore_revlog_before_ms,
            **extra_kwargs,
        )

    def on_compute_fsrs_params_done(future: Future) -> None:
        response: scheduler_pb2.ComputeFsrsParamsResponse = future.result()
        new_parameters = list(response.params)

        old_parameters = list(fsrs_parameters)
        parameters_are_the_same = len(new_parameters) == len(old_parameters) and all(
            round(new_param, 4) == round(old_param, 4)
            for new_param, old_param in zip(new_parameters, old_parameters)
        )
        already_optimal = not new_parameters or parameters_are_the_same

        if already_optimal:
            aqt.mw.taskman.run_on_main(
                lambda: tooltip("FSRS parameters are already optimal!", parent=aqt.mw)
            )
        else:
            deck_config = aqt.mw.col.decks.get_config(conf_id)
            deck_config[f"fsrsParams{FSRS_VERSION}"] = new_parameters
            aqt.mw.col.decks.update_config(deck_config)

            aqt.mw.taskman.run_on_main(
                lambda: tooltip(
                    "FSRS parameters optimized successfully!", parent=aqt.mw
                )
            )

        if on_done:
            on_done()

    aqt.mw.taskman.with_progress(
        task=compute_fsrs_params,
        on_done=on_compute_fsrs_params_done,
        label="Optimizing FSRS parameters",
        parent=aqt.mw,
    )


def _get_amount_relearning_steps_in_day(deck_config: DeckConfigDict) -> int:
    # Ported from TS code
    # https://github.com/ankitects/anki/blob/main/ts/routes/deck-options/FsrsOptions.svelte
    num_of_relearning_steps_in_day = 0
    accumulated_time = 0
    for step in deck_config["lapse"]["delays"]:
        accumulated_time += step
        if accumulated_time >= 24 * 60:  # minutes in a day
            break
        num_of_relearning_steps_in_day += 1

    return num_of_relearning_steps_in_day
