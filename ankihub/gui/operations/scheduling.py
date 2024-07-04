import uuid
from typing import Callable, List, Optional

import aqt
from anki.utils import ids2str
from aqt.operations.scheduling import suspend_cards, unsuspend_cards

from ... import LOGGER
from ...db import ankihub_db
from ...main.utils import truncated_list


def suspend_notes(
    ah_nids: List[uuid.UUID], on_done: Optional[Callable[[], None]] = None
) -> None:
    """Suspend cards of notes in Anki based on their AnkiHub note IDs."""
    _change_suspension_state_of_notes(ah_nids, suspend=True, on_done=on_done)


def unsuspend_notes(
    ah_nids: List[uuid.UUID], on_done: Optional[Callable[[], None]] = None
) -> None:
    """Unsuspend cards of notes in Anki based on their AnkiHub note IDs."""
    _change_suspension_state_of_notes(ah_nids, suspend=False, on_done=on_done)


def _change_suspension_state_of_notes(
    ah_nids: List[uuid.UUID], suspend: bool, on_done: Optional[Callable[[], None]]
) -> None:
    anki_nids = [
        nid for nid in ankihub_db.ankihub_nids_to_anki_nids(ah_nids).values() if nid
    ]
    anki_cids = aqt.mw.col.db.list(
        f"SELECT id FROM cards WHERE nid IN {ids2str(anki_nids)}"
    )
    if not anki_cids:  # pragma: no cover
        LOGGER.info(
            "No cards to change suspension state for",
            suspend=suspend,
            ah_nids_truncated=truncated_list(ah_nids, 3),
        )
        return

    def on_success(_) -> None:
        LOGGER.info(
            "Changed suspension state notes of notes",
            suspend=suspend,
            anki_cids_count=len(anki_cids),
            ah_nids_truncated=truncated_list(ah_nids, 3),
        )
        if on_done:
            on_done()

    def on_failure(exception: Exception) -> None:  # pragma: no cover
        LOGGER.exception(
            f"Failed to change suspension state of notes: {exception}",
            suspend=suspend,
            ah_nids_truncated=truncated_list(ah_nids, 3),
        )
        raise exception

    if suspend:
        operation = suspend_cards
    else:
        operation = unsuspend_cards

    aqt.mw.taskman.run_on_main(
        lambda: operation(parent=aqt.mw, card_ids=anki_cids)
        .success(on_success)
        .failure(on_failure)
        .run_in_background()
    )
