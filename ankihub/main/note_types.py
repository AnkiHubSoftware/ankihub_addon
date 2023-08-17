import uuid
from typing import Dict, Iterable, List

from anki.models import NotetypeDict, NotetypeId

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client.models import NoteInfo
from ..db import ankihub_db


def fetch_note_types_based_on_notes(
    notes_data: List[NoteInfo],
) -> Dict[NotetypeId, NotetypeDict]:
    mids = set(NotetypeId(note_data.mid) for note_data in notes_data)
    result = _fetch_note_types(mids)
    return result


def fetch_note_types_based_on_notes_in_db(
    ankihub_did: uuid.UUID,
) -> Dict[NotetypeId, NotetypeDict]:
    mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
    result = _fetch_note_types(mids)
    return result


def _fetch_note_types(mids: Iterable[NotetypeId]) -> Dict[NotetypeId, NotetypeDict]:
    client = AnkiHubClient()
    result = {mid: client.get_note_type(mid) for mid in mids}
    return result
