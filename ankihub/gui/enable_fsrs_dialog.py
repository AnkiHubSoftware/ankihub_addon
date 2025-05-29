from datetime import date
from typing import Optional

import aqt

from .. import LOGGER
from ..settings import ANKI_INT_VERSION, MIN_ANKI_VERSION_FOR_FSRS_FEATURES, config
from .utils import show_dialog

ENABLE_FSRS_REMINDER_INTERVAL_DAYS = 30


def maybe_show_enable_fsrs_reminder() -> None:
    if ANKI_INT_VERSION < MIN_ANKI_VERSION_FOR_FSRS_FEATURES:
        return

    if not config.get_feature_flags().get("fsrs_reminder", False):
        return

    anking_deck_id = config.anking_deck_id
    if not bool(config.deck_config(anking_deck_id)):
        return

    if aqt.mw.col.get_config("fsrs"):
        return

    if not config._private_config.show_enable_fsrs_reminder:
        return

    days_since_last_reminder = config.get_days_since_last_enable_fsrs_reminder()
    today_date = date.today()
    if (
        days_since_last_reminder is None
        or days_since_last_reminder >= ENABLE_FSRS_REMINDER_INTERVAL_DAYS
    ):
        config.set_last_enable_fsrs_reminder_date(today_date)
        _show_enable_fsrs_reminder()


def _show_enable_fsrs_reminder() -> None:
    def on_button_clicked(button_idx: Optional[int]) -> None:
        enable = button_idx == 1

        dont_show_again = dialog.checkbox.isChecked()

        LOGGER.info(
            "enable_fsrs_reminder_dialog_choice",
            user_choice="enable" if enable else "skip",
            dont_show_again=dont_show_again,
        )

        if dont_show_again:
            config.set_show_enable_fsrs_reminder(False)

        if enable:
            aqt.mw.col.set_config("fsrs", True)

    dialog = show_dialog(
        text="""
            <h3>⚙️ Enable FSRS for Smarter Reviews</h3>
            <p><a href="https://docs.ankiweb.net/deck-options.html#fsrs">FSRS</a>
            (Free Spaced Repetition Scheduler) is a new,
            smarter scheduling system <strong>recommended by The AnKing.</strong></p>
            <p>It adapts to your learning pace and customizes your review intervals
            — helping you study less and remember more.</p>

            <p><strong>Want us to enable FSRS for you for all decks?</strong></p>
            <br>
            """,
        title="AnKing Recommendation",
        buttons=[
            ("Skip", aqt.QDialogButtonBox.ButtonRole.RejectRole),
            ("Enable FSRS", aqt.QDialogButtonBox.ButtonRole.AcceptRole),
        ],
        default_button_idx=1,
        callback=on_button_clicked,
        open_dialog=False,
        add_title_to_body_on_mac=False,
        parent=aqt.mw,
        checkbox=aqt.QCheckBox("Don't show this again"),
    )

    dialog.show()
