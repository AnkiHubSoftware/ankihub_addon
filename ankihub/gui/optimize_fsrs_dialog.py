from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Callable, Optional

import aqt
from anki import scheduler_pb2
from anki.decks import DeckConfigDict, DeckConfigId
from aqt.qt import QCheckBox, QDialogButtonBox
from aqt.utils import tooltip

from .. import LOGGER
from ..main.deck_options import fsrs_parameters_equal, get_fsrs_parameters
from ..main.utils import get_deck_for_ah_did
from ..settings import (
    ANKI_INT_VERSION,
    FSRS_VERSION,
    MIN_ANKI_VERSION_FOR_FSRS_FEATURES,
    config,
)
from .utils import show_dialog

FSRS_OPTIMIZATION_REMINDER_INTERVAL_DAYS = 30


def maybe_show_fsrs_optimization_reminder() -> None:
    if not config.get_feature_flags().get("fsrs_reminder", False):
        return

    if not (anking_deck := get_deck_for_ah_did(config.anking_deck_id)):
        return

    deck_configs_for_update = aqt.mw.col.decks.get_deck_configs_for_update(anking_deck["id"])
    if not (
        ANKI_INT_VERSION >= MIN_ANKI_VERSION_FOR_FSRS_FEATURES
        and deck_configs_for_update.fsrs
        and config.public_config.get("remind_to_optimize_fsrs_parameters", False)
    ):
        return

    days_since_last_reminder = config.get_days_since_last_fsrs_optimize_reminder()
    reminder_interval_met = (
        days_since_last_reminder is None or days_since_last_reminder >= FSRS_OPTIMIZATION_REMINDER_INTERVAL_DAYS
    )
    optimize_interval_met = (
        # days_since_last_fsrs_optimize is a global value, not just for the current deck, but that's okay, because
        # if the user optimized the parameters for some deck, they probably don't need the reminder
        deck_configs_for_update.days_since_last_fsrs_optimize >= FSRS_OPTIMIZATION_REMINDER_INTERVAL_DAYS
    )
    if reminder_interval_met and optimize_interval_met:
        try:
            _show_fsrs_optimization_reminder()
            config.set_last_fsrs_optimization_reminder_date(datetime.now())
        except Exception as e:
            LOGGER.exception(
                "Error showing FSRS optimization reminder dialog",
                exc_info=e,
            )


def _show_fsrs_optimization_reminder() -> None:
    if not (anking_deck := get_deck_for_ah_did(config.anking_deck_id)):
        return

    anki_did = anking_deck["id"]

    def on_button_clicked(button_idx: Optional[int]) -> None:
        optimize = button_idx == 1

        dont_show_again = dialog.checkbox.isChecked()

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

        if aqt.mw.col.decks.get(anki_did, default=False) is None:
            return

        conf_id = aqt.mw.col.decks.config_dict_for_deck_id(anki_did)["id"]
        _optimize_fsrs_parameters(conf_id)

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
        checkbox=QCheckBox("Don't show this again"),
        callback=on_button_clicked,
        open_dialog=False,
        add_title_to_body_on_mac=False,
        parent=aqt.mw,
    )
    dialog.show()


def _optimize_fsrs_parameters(conf_id: DeckConfigId, on_done: Optional[Callable[[], None]] = None) -> None:
    deck_config = aqt.mw.col.decks.get_config(conf_id)

    _, fsrs_parameters = get_fsrs_parameters(conf_id)

    def compute_fsrs_params() -> scheduler_pb2.ComputeFsrsParamsResponse:
        deck_config_name_escaped = deck_config["name"].replace("\\", "\\\\").replace('"', '\\"')
        default_search = f'preset:"{deck_config_name_escaped}" -is:suspended'

        ignore_revlog_before_date_str = deck_config.get("ignoreRevlogsBeforeDate") or "1970-01-01"
        ignore_revlog_before_date = datetime.fromisoformat(ignore_revlog_before_date_str).replace(tzinfo=timezone.utc)
        ignore_revlog_before_ms = int(ignore_revlog_before_date.timestamp() * 1000)

        extra_kwargs = {}
        if ANKI_INT_VERSION >= 250200:
            # The num_of_relearning_steps parameter became available in Anki 25.02
            extra_kwargs["num_of_relearning_steps"] = _get_amount_relearning_steps_in_day(
                deck_config,
            )
        if ANKI_INT_VERSION >= 250700:
            extra_kwargs["health_check"] = False

        return aqt.mw.col.backend.compute_fsrs_params(
            search=deck_config.get("paramSearch", deck_config.get("weightSearch", default_search)),
            current_params=fsrs_parameters,
            ignore_revlogs_before_ms=ignore_revlog_before_ms,
            **extra_kwargs,
        )

    def on_compute_fsrs_params_done(future: Future) -> None:
        response: scheduler_pb2.ComputeFsrsParamsResponse = future.result()
        new_parameters = list(response.params)
        old_parameters = list(fsrs_parameters)

        already_optimal = not new_parameters or fsrs_parameters_equal(old_parameters, new_parameters)
        if already_optimal:
            aqt.mw.taskman.run_on_main(lambda: tooltip("FSRS parameters are already optimal!", parent=aqt.mw))
        else:
            deck_config = aqt.mw.col.decks.get_config(conf_id)
            deck_config[f"fsrsParams{FSRS_VERSION}"] = new_parameters
            aqt.mw.col.decks.update_config(deck_config)

            aqt.mw.taskman.run_on_main(lambda: tooltip("FSRS parameters optimized successfully!", parent=aqt.mw))

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
