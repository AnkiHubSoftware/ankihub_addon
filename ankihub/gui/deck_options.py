from concurrent.futures import Future
from datetime import datetime, timezone

import aqt
from anki import scheduler_pb2
from anki.decks import DeckConfigDict, DeckConfigId
from aqt import mw
from aqt.qt import QCheckBox
from aqt.utils import tooltip

from ..main.deck_options import get_fsrs_parameters
from ..settings import FSRS_VERSION, config
from .utils import show_dialog


def optimize_fsrs_parameters(conf_id: DeckConfigId) -> None:
    deck_config = aqt.mw.col.decks.get_config(conf_id)

    _, fsrs_parameters = get_fsrs_parameters(conf_id)

    def compute_fsrs_params() -> scheduler_pb2.ComputeFsrsParamsResponse:

        deck_config_name_escaped = (
            deck_config["name"].replace("\\", "\\\\").replace('"', '\\"')
        )
        default_search = f'preset:"{deck_config_name_escaped}" -is:suspended'

        ignore_revlog_before_date_str = deck_config.get("ignoreRevlogsBeforeDate")
        ignore_revlog_before_date = datetime.fromisoformat(
            ignore_revlog_before_date_str
        ).replace(tzinfo=timezone.utc)
        ignore_revlog_before_ms = int(ignore_revlog_before_date.timestamp() * 1000)

        return aqt.mw.col.backend.compute_fsrs_params(
            search=deck_config.get("weightSearch", default_search),
            current_params=fsrs_parameters,
            ignore_revlogs_before_ms=ignore_revlog_before_ms,
            num_of_relearning_steps=_get_amount_relearning_steps_in_day(
                deck_config,
            ),
        )

    def on_done(future: Future) -> None:
        response: scheduler_pb2.ComputeFsrsParamsResponse = future.result()
        params = list(response.params)

        already_optimal = not params or [
            round(param, 4) == round(old_param, 4)
            for param, old_param in zip(params, fsrs_parameters)
        ]

        if already_optimal:
            aqt.mw.taskman.run_on_main(
                lambda: tooltip("FSRS parameters are already optimal!", parent=aqt.mw)
            )
            return

        deck_config = aqt.mw.col.decks.get_config(conf_id)
        deck_config[f"fsrsParams{FSRS_VERSION}"] = params
        aqt.mw.col.decks.update_config(deck_config)

        mw.taskman.run_on_main(
            lambda: tooltip("FSRS parameters optimized successfully!", parent=aqt.mw)
        )

    aqt.mw.taskman.with_progress(
        task=compute_fsrs_params,
        on_done=on_done,
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


def show_fsrs_optimization_reminder() -> None:
    deck_config = config.deck_config(config.anking_deck_id)
    if not deck_config:
        return

    anki_did = deck_config.anki_id
    if aqt.mw.col.decks.get(anki_did) is None:
        return

    def on_button_clicked(button_index: int):
        assert isinstance(dialog.dont_show_this_again_cb, QCheckBox)

        if dialog.dont_show_this_again_cb.isChecked():
            # TODO
            pass

        if button_index == 0:
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
        buttons=["Skip", "Optimize"],
        default_button_idx=1,
        callback=on_button_clicked,
        open_dialog=False,
    )

    dialog.dont_show_this_again_cb = QCheckBox("Don't show this again")
    layout = dialog.content_layout
    layout.insertWidget(
        layout.count() - 2,
        dialog.dont_show_this_again_cb,
    )
    dialog.adjustSize()

    dialog.show()
