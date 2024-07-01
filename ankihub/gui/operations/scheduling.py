import uuid
from typing import List

import aqt
from anki.utils import ids2str
from aqt.operations.scheduling import suspend_cards, unsuspend_cards

from ... import LOGGER
from ...db import ankihub_db
from ...main.utils import truncated_list


def suspend_notes(ah_nids: List[uuid.UUID]) -> None:
    anki_nids = [
        nid for nid in ankihub_db.ankihub_nids_to_anki_nids(ah_nids).values() if nid
    ]
    anki_cids = aqt.mw.col.db.list(
        f"SELECT id FROM cards WHERE nid IN {ids2str(anki_nids)}"
    )
    if not anki_cids:
        LOGGER.info("No cards to suspend", ah_nids_truncated=truncated_list(ah_nids, 3))
        return

    def on_success(_) -> None:
        LOGGER.info(
            "Suspended notes",
            anki_cids_count=len(anki_cids),
            ah_nids_truncated=truncated_list(anki_nids),
        )

    def on_failure(exception: Exception) -> None:  # pragma: no cover
        LOGGER.exception(
            f"Failed to suspend notes: {exception}",
            ah_nids_truncated=truncated_list(anki_nids),
        )
        raise exception

    aqt.mw.taskman.run_on_main(
        lambda: suspend_cards(parent=aqt.mw, card_ids=anki_cids)
        .success(on_success)
        .failure(on_failure)
        .run_in_background()
    )


def unsuspend_notes(ah_nids: List[uuid.UUID]) -> None:
    anki_nids = [
        nid for nid in ankihub_db.ankihub_nids_to_anki_nids(ah_nids).values() if nid
    ]
    anki_cids = aqt.mw.col.db.list(
        f"SELECT id FROM cards WHERE nid IN {ids2str(anki_nids)}"
    )
    if not anki_cids:
        LOGGER.info(
            "No cards to unsuspend", ah_nids_truncated=truncated_list(ah_nids, 3)
        )
        return

    def on_success(_) -> None:
        LOGGER.info(
            "Unsuspended notes",
            anki_cids_count=len(anki_cids),
            ah_nids_truncated=truncated_list(anki_nids),
        )

    def on_failure(exception: Exception) -> None:  # pragma: no cover
        LOGGER.exception(
            f"Failed to unsuspend notes: {exception}",
            ah_nids_truncated=truncated_list(anki_nids),
        )
        raise exception

    aqt.mw.taskman.run_on_main(
        lambda: unsuspend_cards(parent=aqt.mw, card_ids=anki_cids)
        .success(on_success)
        .failure(on_failure)
        .run_in_background()
    )
