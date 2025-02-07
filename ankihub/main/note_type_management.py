import uuid
from typing import List

import aqt
from anki.models import NotetypeDict

from ..db import ankihub_db


# TODO
def add_note_type(note_type: NotetypeDict) -> None:
    print("add_note_type", note_type["name"])


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


def update_deck_templates(ah_did: uuid.UUID) -> None:
    print("update_deck_templates", ah_did)
