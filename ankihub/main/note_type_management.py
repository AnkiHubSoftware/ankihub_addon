import copy
import uuid
from typing import Any, Dict, List

import aqt
from anki.models import NotetypeDict, NotetypeId

from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .utils import (
    ANKIHUB_CSS_END_COMMENT_PATTERN,
    ANKIHUB_HTML_END_COMMENT_PATTERN,
    modified_note_type,
)


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


def add_note_type_fields(
    ah_did: uuid.UUID, note_type: NotetypeDict, new_field_names: List[str]
) -> NotetypeDict:
    client = AddonAnkiHubClient()

    db_note_type = ankihub_db.note_type_dict(ah_did, note_type["id"])
    new_fields = [
        field for field in note_type["flds"] if field["name"] in new_field_names
    ]
    db_note_type["flds"].extend(new_fields)
    for db_field in db_note_type["flds"]:
        field = next(
            (field for field in note_type["flds"] if field["name"] == db_field["name"]),
            None,
        )
        if field:
            db_field["ord"] = field["ord"]
    ankihub_id_field_idx = next(
        (
            idx
            for idx, field in enumerate(db_note_type["flds"])
            if field["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME
        ),
        None,
    )
    if ankihub_id_field_idx is not None:
        db_note_type["flds"][ankihub_id_field_idx]["ord"] = (
            len(db_note_type["flds"]) - 1
        )
        ankihub_id_field = db_note_type["flds"].pop(ankihub_id_field_idx)
        db_note_type["flds"].append(ankihub_id_field)
    db_note_type = client.update_note_type(ah_did, db_note_type, ["flds"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=db_note_type)

    return db_note_type


def note_type_with_ankihub_end_comment_removed(
    note_type: Dict[str, Any]
) -> Dict[str, Any]:
    note_type = copy.deepcopy(note_type)
    note_type["css"] = ANKIHUB_CSS_END_COMMENT_PATTERN.sub("", note_type["css"])
    for template in note_type["tmpls"]:
        template["qfmt"] = ANKIHUB_HTML_END_COMMENT_PATTERN.sub("", template["qfmt"])
        template["afmt"] = ANKIHUB_HTML_END_COMMENT_PATTERN.sub("", template["afmt"])

    return note_type


def note_types_with_template_changes_for_deck(ah_did: uuid.UUID) -> List[NotetypeId]:
    ids = []
    for mid in ankihub_db.note_types_for_ankihub_deck(ah_did):
        changed = False
        db_note_type = note_type_with_ankihub_end_comment_removed(
            ankihub_db.note_type_dict(ah_did, mid)
        )
        note_type = note_type_with_ankihub_end_comment_removed(
            aqt.mw.col.models.get(mid)
        )
        if note_type["css"] != db_note_type["css"]:
            changed = True
        elif len(note_type["tmpls"]) != len(db_note_type["tmpls"]):
            changed = True
        else:
            for i, tmpl in enumerate(note_type["tmpls"]):
                if tmpl != db_note_type["tmpls"][i]:
                    changed = True
                    break
        if changed:
            ids.append(mid)
    return ids


def new_fields_for_note_type(ah_did: uuid.UUID, note_type: NotetypeDict) -> List[str]:
    field_names = aqt.mw.col.models.field_names(note_type)
    ankihub_field_names = ankihub_db.note_type_field_names(ah_did, note_type["id"])
    new_fields = [name for name in field_names if name not in ankihub_field_names]
    return new_fields


def update_note_type_templates_and_styles(
    ah_did: uuid.UUID, note_type: NotetypeDict
) -> NotetypeDict:
    client = AddonAnkiHubClient()
    note_type = note_type_with_ankihub_end_comment_removed(note_type)
    db_note_type = ankihub_db.note_type_dict(ah_did, note_type["id"])

    db_note_type["tmpls"] = note_type["tmpls"]
    db_note_type["css"] = note_type["css"]

    db_note_type = client.update_note_type(ah_did, db_note_type, ["css", "tmpls"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=db_note_type)

    return db_note_type
