import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import aqt
from anki import consts as anki_consts

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import CardReviewData
from ..ankihub_client.models import DailyCardReviewSummary
from ..db import ankihub_db
from ..settings import config, get_end_cutoff_date_for_sending_review_summaries


def send_review_data() -> None:
    """Send data about card reviews for each installed AnkiHub deck to the server.
    Data about decks that have not been reviewed yet will not be included."""
    LOGGER.info("Sending review data to AnkiHub...")

    now = datetime.now()
    card_review_data: List[CardReviewData] = []
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
        WHERE r.id > ?
            AND r.type != {anki_consts.REVLOG_RESCHED}
            AND c.nid IN ({','.join(map(str, anki_nids))})
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
        WHERE r.type != {anki_consts.REVLOG_RESCHED}
            AND c.nid IN ({','.join(map(str, anki_nids))})
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


def get_daily_review_summaries_since_last_sync(
    last_sent_summary_date: date,
) -> List[DailyCardReviewSummary]:
    """Filter revlog entries between the date of the last sync and the end of yesterday
    group by days, and compile the data."""
    start_of_day_after_last_sent_summary_date = datetime.combine(
        last_sent_summary_date + timedelta(days=1), datetime.min.time()
    )
    timeframe_end = datetime.combine(
        get_end_cutoff_date_for_sending_review_summaries(),
        datetime.max.time(),
    )

    rows = aqt.mw.col.db.all(
        """
        SELECT r.id, r.ease, r.time
        FROM revlog AS r
        JOIN cards AS c ON r.cid = c.id
        WHERE r.id BETWEEN ? AND ?
        AND r.type != ?
        """,
        int(datetime.timestamp(start_of_day_after_last_sent_summary_date)) * 1000,
        int(datetime.timestamp(timeframe_end)) * 1000,
        anki_consts.REVLOG_RESCHED,
    )

    daily_reviews = defaultdict(list)

    for row in rows:
        review_timestamp = int(row[0])
        review_ease = int(row[1])
        review_time = int(row[2])
        review_date = _ms_timestamp_to_datetime(review_timestamp).date()
        daily_reviews[review_date].append((review_ease, review_time))

    daily_card_review_data = []
    for key, item in daily_reviews.items():
        total_cards_studied = len(item)
        total_time_reviewing = sum(time for _, time in item)
        total_cards_marked_as_again = sum(1 for ease, _ in item if ease == 1)
        total_cards_marked_as_hard = sum(1 for ease, _ in item if ease == 2)
        total_cards_marked_as_good = sum(1 for ease, _ in item if ease == 3)
        total_cards_marked_as_easy = sum(1 for ease, _ in item if ease == 4)

        daily_card_review_data.append(
            DailyCardReviewSummary(
                total_cards_studied=total_cards_studied,
                total_time_reviewing=total_time_reviewing,
                total_cards_marked_as_again=total_cards_marked_as_again,
                total_cards_marked_as_hard=total_cards_marked_as_hard,
                total_cards_marked_as_good=total_cards_marked_as_good,
                total_cards_marked_as_easy=total_cards_marked_as_easy,
                review_session_date=key,
            )
        )
    return daily_card_review_data


def send_daily_review_summaries(last_summary_sent_date: date) -> None:
    """Send daily review summaries to the server."""
    daily_review_summaries = get_daily_review_summaries_since_last_sync(
        last_summary_sent_date
    )
    client = AnkiHubClient()
    if daily_review_summaries:
        client.send_daily_card_review_summaries(daily_review_summaries)
        LOGGER.info("Daily review summaries sent to AnkiHub.")
    else:
        LOGGER.info("No daily review summaries to send to AnkiHub.")
