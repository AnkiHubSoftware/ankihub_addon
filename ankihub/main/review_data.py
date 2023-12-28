import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

import aqt

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import CardReviewData
from ..db import ankihub_db
from ..settings import config


def send_review_data() -> None:
    """Send data about card reviews for each installed AnkiHub deck to the server.
    Data about decks that have not been reviewed yet will not be included."""
    now = datetime.now()
    card_review_data = []
    for ah_did in config.deck_ids():
        first_and_last_review_times = _get_first_and_last_review_datetime_for_ah_deck(
            ah_did
        )
        if first_and_last_review_times is None:
            # This deck has no reviews yet
            continue

        first_review_at, last_review_at = first_and_last_review_times
        total_card_reviews_last_7_days = _get_review_count_for_ah_deck_since(
            ah_did, now - timedelta(days=7)
        )
        total_card_reviews_last_30_days = _get_review_count_for_ah_deck_since(
            ah_did, now - timedelta(days=30)
        )
        card_review_data.append(
            CardReviewData(
                ah_did=ah_did,
                total_card_reviews_last_7_days=total_card_reviews_last_7_days,
                total_card_reviews_last_30_days=total_card_reviews_last_30_days,
                first_card_review_at=first_review_at,
                last_card_review_at=last_review_at,
            )
        )

    LOGGER.info(f"Review data: {card_review_data}")

    client = AnkiHubClient()
    client.send_card_review_data(card_review_data)


def _get_review_count_for_ah_deck_since(ah_did: uuid.UUID, since: datetime) -> int:
    """Get the number of reviews (recorded in Anki's review log table) for an ankihub deck since a given time."""
    timestamp_ms = int(datetime.timestamp(since) * 1000)
    anki_nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
    result = aqt.mw.col.db.scalar(
        f"""
        SELECT COUNT(*)
        FROM revlog as r
        JOIN cards as c ON r.cid = c.id
        WHERE r.id > ? AND c.nid IN ({','.join(map(str, anki_nids))})
        """,
        timestamp_ms,
    )
    return result


def _get_first_and_last_review_datetime_for_ah_deck(
    ah_did: uuid.UUID,
) -> Optional[Tuple[datetime, datetime]]:
    """Get the date time of the first and last review (recorded in Anki's review log table) for an ankihub deck."""
    anki_nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
    row = aqt.mw.col.db.first(
        f"""
        SELECT MIN(r.id), MAX(r.id)
        FROM revlog as r
        JOIN cards as c ON r.cid = c.id
        WHERE c.nid IN ({','.join(map(str, anki_nids))})
        """,
    )
    if row[0] is None:
        return None

    first_timestamp_str, last_timestamp_str = row
    first_review_datetime = _ms_timestamp_to_datetime(int(first_timestamp_str))
    last_review_datetime = _ms_timestamp_to_datetime(int(last_timestamp_str))
    return first_review_datetime, last_review_datetime


def _ms_timestamp_to_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp / 1000)
