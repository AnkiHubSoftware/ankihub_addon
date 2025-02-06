import uuid
from typing import List

import aqt
from anki.models import NotetypeDict

from ..db import ankihub_db


# TODO
def add_notetype(notetype: NotetypeDict) -> None:
    print("add_notetype", notetype["name"])


def update_notetype_fields(notetype: NotetypeDict, fields: List[str]) -> None:
    print("update_notetype_fields", notetype["name"], fields)


def deck_has_template_changes(ah_did: uuid.UUID) -> bool:
    for mid in ankihub_db.note_types_for_ankihub_deck(ah_did):
        db_notetype = ankihub_db.note_type_dict(ah_did, mid)
        notetype = aqt.mw.col.models.get(mid)
        if notetype["css"] != db_notetype["css"]:
            return True
        if len(notetype["tmpls"]) != len(db_notetype["tmpls"]):
            return True
        else:
            for i, tmpl in enumerate(notetype["tmpls"]):
                if tmpl != db_notetype["tmpls"][i]:
                    return True

    return False


def update_deck_templates(ah_did: uuid.UUID) -> None:
    print("update_deck_templates", ah_did)
