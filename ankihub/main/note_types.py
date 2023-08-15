import uuid
from typing import Dict

from anki.models import NotetypeDict, NotetypeId

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..db import ankihub_db


def get_note_types_for_deck(ankihub_did: uuid.UUID) -> Dict[NotetypeId, NotetypeDict]:
    mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
    client = AnkiHubClient()
    result = {mid: client.get_note_type(mid) for mid in mids}
    return result
