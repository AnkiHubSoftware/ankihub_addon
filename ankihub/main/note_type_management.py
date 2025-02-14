import uuid
from typing import List

import aqt
from anki.models import NotetypeDict, NotetypeId

from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from .utils import modified_note_type


def add_note_type(ah_did: uuid.UUID, note_type: NotetypeDict) -> NotetypeDict:
    client = AddonAnkiHubClient()

    new_note_type = modified_note_type(note_type)
    new_note_type["id"] = 0
    # Add note type first to get a unique ID
    new_mid = aqt.mw.col.models.add_dict(new_note_type).id
    new_note_type = aqt.mw.col.models.get(NotetypeId(new_mid))
    # Send base name to AnkiHub, as it will take care of adding the deck name and username
    new_note_type["name"] = note_type["name"]
    new_name = client.create_note_type(ah_did, new_note_type)["name"]
    new_note_type["name"] = new_name
    aqt.mw.col.models.update_dict(new_note_type)
    new_note_type = aqt.mw.col.models.get(NotetypeId(new_mid))
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=new_note_type)

    return new_note_type


def update_note_type_fields(note_type: NotetypeDict, fields: List[str]) -> None:
    print("update_note_type_fields", note_type["name"], fields)


def deck_has_template_changes(ah_did: uuid.UUID) -> bool:
    for mid in ankihub_db.note_types_for_ankihub_deck(ah_did):
        db_note_type = ankihub_db.note_type_dict(ah_did, mid)
        note_type = aqt.mw.col.models.get(mid)
        if note_type["css"] != db_note_type["css"]:
            return True
        if len(note_type["tmpls"]) != len(db_note_type["tmpls"]):
            return True
        for i, tmpl in enumerate(note_type["tmpls"]):
            if tmpl != db_note_type["tmpls"][i]:
                return True

    return False


def update_deck_templates(ah_did: uuid.UUID, note_type: NotetypeDict) -> None:
    print("update_deck_templates", ah_did)
