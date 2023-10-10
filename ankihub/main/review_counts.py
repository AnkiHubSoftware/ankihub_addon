import uuid
from datetime import datetime, timedelta

import aqt

from .. import LOGGER
from ..db import attached_ankihub_db
from ..settings import config

# The server needs the review counts for the last 30 days
REVIEW_PERIOD_DAYS = timedelta(days=30)


def send_review_counts() -> None:
    since = datetime.now() - REVIEW_PERIOD_DAYS
    review_count_per_deck = {}
    for ah_did in config.deck_ids():
        review_count_per_deck[ah_did] = _get_review_count_for_ah_deck_since(
            ah_did, since
        )

    # TODO Send review counts to AnkiHub
    LOGGER.info(f"Review counts: {review_count_per_deck}")


def _get_review_count_for_ah_deck_since(ah_did: uuid.UUID, since: datetime) -> int:
    """Get the number of reviews (recorded in Anki's review log table) for an ankihub deck since a given time."""
    timestamp_ms = datetime.timestamp(since) * 1000
    with attached_ankihub_db():
        result = aqt.mw.col.db.scalar(
            """
            SELECT COUNT(*)
            FROM revlog as r
            JOIN cards as c ON r.cid = c.id
            JOIN ankihub_db.notes as ah_n ON c.nid = ah_n.anki_note_id
            WHERE r.id > ? AND ah_n.ankihub_deck_id = ?
            """,
            timestamp_ms,
            str(ah_did),
        )
    return result
