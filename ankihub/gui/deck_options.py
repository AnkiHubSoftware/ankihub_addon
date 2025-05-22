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


def optimize_fsrs_parameters(conf_id: DeckConfigId):
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

        print("FSRS params:", [round(param, 4) for param in params])

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

        # open_deck_options_dialog_and_scroll_to_fsrs(anki_did)

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


a = {
    "id": 1730584908073,
    "mod": 1747844944,
    "name": "JKU",
    "usn": -1,
    "maxTaken": 60,
    "autoplay": True,
    "timer": 0,
    "replayq": True,
    "new": {
        "bury": True,
        "delays": [10.0],
        "initialFactor": 2400,
        "ints": [1, 4, 0],
        "order": 1,
        "perDay": 30,
        "retirementActions": {
            "delete": False,
            "move": False,
            "suspend": True,
            "tag": True,
        },
        "retiringInterval": 0,
        "separate": True,
    },
    "rev": {
        "bury": True,
        "ease4": 1.3,
        "ivlFct": 1.0,
        "maxIvl": 36500,
        "perDay": 999,
        "hardFactor": 1.2,
        "minSpace": 1,
        "fuzz": 0.05,
    },
    "lapse": {
        "delays": [10.0],
        "leechAction": 0,
        "leechFails": 8,
        "minInt": 1,
        "mult": 0.5,
    },
    "dyn": False,
    "newMix": 1,
    "newPerDayMinimum": 0,
    "interdayLearningMix": 2,
    "reviewOrder": 11,
    "newSortOrder": 1,
    "newGatherPriority": 4,
    "buryInterdayLearning": True,
    "fsrsWeights": [],
    "fsrsParams5": [],
    "fsrsParams6": [
        0.48438278,
        2.6989408,
        8.904372,
        24.615675,
        7.137523,
        0.56907123,
        2.1679354,
        0.001,
        1.4669979,
        0.16145188,
        0.9536771,
        1.8758221,
        0.12884021,
        0.39521834,
        2.2978072,
        0.051171675,
        3.0004,
        0.73922735,
        0.34591433,
        0.13614067,
        0.1,
    ],
    "desiredRetention": 0.9,
    "ignoreRevlogsBeforeDate": "1970-01-01",
    "easyDaysPercentages": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "stopTimerOnAnswer": False,
    "secondsToShowQuestion": 0.0,
    "secondsToShowAnswer": 0.0,
    "questionAction": 0,
    "answerAction": 0,
    "waitForAudio": False,
    "sm2Retention": 0.9,
    "weightSearch": "deck:JKU",
    "exam_settings": {"enabled": False, "exam_date": 1650812661, "exam_name": ""},
    "fsrsParams4": [],
}
