import uuid
from typing import List

import aqt
from anki.models import NotetypeDict, NotetypeId

from ..addon_ankihub_client import AddonAnkiHubClient
from ..db import ankihub_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .utils import modified_note_type, note_type_without_ankihub_modifications


def add_note_type(ah_did: uuid.UUID, note_type: NotetypeDict) -> NotetypeDict:
    client = AddonAnkiHubClient()

    new_note_type = modified_note_type(note_type)
    new_note_type["id"] = 0
    # Add note type first to get a unique ID
    new_mid = NotetypeId(aqt.mw.col.models.add_dict(new_note_type).id)
    new_note_type = aqt.mw.col.models.get(new_mid)
    # Send base name to AnkiHub, as it will take care of adding the deck name and username
    new_note_type["name"] = note_type["name"]
    try:
        new_name = client.create_note_type(ah_did, new_note_type)["name"]
    except Exception as e:
        aqt.mw.col.models.remove(new_mid)
        raise e
    new_note_type["name"] = new_name
    aqt.mw.col.models.update_dict(new_note_type)
    new_note_type = aqt.mw.col.models.get(NotetypeId(new_mid))
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=new_note_type)

    return new_note_type


def add_note_type_fields(
    ah_did: uuid.UUID, note_type: NotetypeDict, new_field_names: List[str]
) -> NotetypeDict:
    client = AddonAnkiHubClient()

    ah_note_type = ankihub_db.note_type_dict(ah_did, note_type["id"])
    new_fields = [
        field for field in note_type["flds"] if field["name"] in new_field_names
    ]
    ah_note_type["flds"].extend(new_fields)
    for db_field in ah_note_type["flds"]:
        field = next(
            (field for field in note_type["flds"] if field["name"] == db_field["name"]),
            None,
        )
        if field:
            db_field["ord"] = field["ord"]
    ankihub_id_field_idx = next(
        (
            idx
            for idx, field in enumerate(ah_note_type["flds"])
            if field["name"] == ANKIHUB_NOTE_TYPE_FIELD_NAME
        ),
        None,
    )
    if ankihub_id_field_idx is not None:
        ah_note_type["flds"][ankihub_id_field_idx]["ord"] = (
            len(ah_note_type["flds"]) - 1
        )
        ankihub_id_field = ah_note_type["flds"].pop(ankihub_id_field_idx)
        ah_note_type["flds"].append(ankihub_id_field)
    ah_note_type = client.update_note_type(ah_did, ah_note_type, ["flds"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=ah_note_type)

    return ah_note_type


def note_types_with_template_changes_for_deck(ah_did: uuid.UUID) -> List[NotetypeId]:
    ids = []
    for mid in ankihub_db.note_types_for_ankihub_deck(ah_did):
        changed = False
        ah_note_type = note_type_without_ankihub_modifications(
            ankihub_db.note_type_dict(ah_did, mid)
        )
        anki_note_type = note_type_without_ankihub_modifications(
            aqt.mw.col.models.get(mid)
        )
        if anki_note_type["css"] != ah_note_type["css"]:
            changed = True
        elif len(anki_note_type["tmpls"]) != len(ah_note_type["tmpls"]):
            changed = True
        else:
            for anki_tmpl, ah_tmpl in zip(
                anki_note_type["tmpls"], ah_note_type["tmpls"]
            ):
                if anki_tmpl != ah_tmpl:
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
    note_type = note_type_without_ankihub_modifications(note_type)
    ah_note_type = ankihub_db.note_type_dict(ah_did, note_type["id"])

    ah_note_type["tmpls"] = note_type["tmpls"]
    ah_note_type["css"] = note_type["css"]

    ah_note_type = client.update_note_type(ah_did, ah_note_type, ["css", "tmpls"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=ah_note_type)

    return ah_note_type
