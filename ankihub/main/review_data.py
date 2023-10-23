import uuid
from datetime import datetime, timedelta
from typing import Optional

import aqt

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import CardReviewData
from ..db import attached_ankihub_db
from ..settings import config

# The server needs the review counts for the last 30 days
REVIEW_PERIOD_DAYS = timedelta(days=30)


def send_review_data() -> None:
    """Send data about card reviews for each installed AnkiHub deck to the server.
    Data about decks that have not been reviewed yet will not be included."""
    since = datetime.now() - REVIEW_PERIOD_DAYS
    card_review_data = [
        CardReviewData(
            ah_did=ah_did,
            total_card_reviews_last_30_days=_get_review_count_for_ah_deck_since(
                ah_did, since
            ),
            last_card_review_at=last_review_time,
        )
        for ah_did in config.deck_ids()
        if (last_review_time := _get_last_review_datetime_for_ah_deck(ah_did))
    ]

    LOGGER.info(f"Review counts: {card_review_data}")

    client = AnkiHubClient()
    client.send_card_review_data(card_review_data)


def _get_review_count_for_ah_deck_since(ah_did: uuid.UUID, since: datetime) -> int:
    """Get the number of reviews (recorded in Anki's review log table) for an ankihub deck since a given time."""
    timestamp_ms = int(datetime.timestamp(since) * 1000)
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


def _get_last_review_datetime_for_ah_deck(ah_did: uuid.UUID) -> Optional[datetime]:
    """Get the date time of the last review (recorded in Anki's review log table) for an ankihub deck."""
    with attached_ankihub_db():
        timestamp_str = aqt.mw.col.db.scalar(
            """
            SELECT MAX(r.id)
            FROM revlog as r
            JOIN cards as c ON r.cid = c.id
            JOIN ankihub_db.notes as ah_n ON c.nid = ah_n.anki_note_id
            WHERE ah_n.ankihub_deck_id = ?
            """,
            str(ah_did),
        )
    if timestamp_str is None:
        return None

    timestamp_ms = int(timestamp_str)
    result = datetime.fromtimestamp(timestamp_ms / 1000)
    return result
