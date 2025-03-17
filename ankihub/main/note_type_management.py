import copy
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
    # Send name as-is to AnkiHub, as it will take care of adding the deck name and username
    new_note_type["name"] = note_type["name"]
    try:
        new_name = client.create_note_type(ah_did, new_note_type)["name"]
    except Exception as e:
        aqt.mw.col.models.remove(new_mid)
        raise e
    new_note_type["name"] = new_name
    aqt.mw.col.models.update_dict(new_note_type)
    new_note_type = aqt.mw.col.models.get(NotetypeId(new_mid))
    # Ensure name in AnkiHub DB is as on AnkiHub, even if Anki changed it to make it unique
    new_note_type["name"] = new_name
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=new_note_type)

    return new_note_type


def add_note_type_fields(
    ah_did: uuid.UUID, note_type: NotetypeDict, new_field_names: List[str]
) -> NotetypeDict:
    note_type = copy.deepcopy(note_type)
    ah_note_type = ankihub_db.note_type_dict(note_type["id"])

    existing_ah_field_names = set(field["name"] for field in ah_note_type["flds"])
    combined_ah_field_names = existing_ah_field_names | set(new_field_names)

    # Create ordered list of field names based on Anki note type ordering
    ordered_field_names = [
        field["name"]
        for field in note_type["flds"]
        if field["name"] in combined_ah_field_names
    ]

    # ... Add fields missing from the Anki note type to the end
    missing_fields_names = combined_ah_field_names - set(ordered_field_names)
    ordered_field_names.extend(missing_fields_names)

    # Ensure ANKIHUB_NOTE_TYPE_FIELD_NAME is the last field
    if ANKIHUB_NOTE_TYPE_FIELD_NAME in ordered_field_names:
        ordered_field_names.remove(ANKIHUB_NOTE_TYPE_FIELD_NAME)
        ordered_field_names.append(ANKIHUB_NOTE_TYPE_FIELD_NAME)

    # Assemble fields in order, taking new fields from the Anki note type,
    # and existing fields from the AnkiHub note type
    updated_fields = []
    for field_name in ordered_field_names:
        source_fields = (
            ah_note_type["flds"]
            if field_name in existing_ah_field_names
            else note_type["flds"]
        )
        updated_fields.append(
            next(field for field in source_fields if field["name"] == field_name)
        )

    # Update field ordinals
    for idx, field in enumerate(updated_fields):
        field["ord"] = idx

    # Update AnkiHub note type with new fields
    ah_note_type["flds"] = updated_fields

    # Update remote and local AnkiHub database
    client = AddonAnkiHubClient()
    ah_note_type = client.update_note_type(ah_did, ah_note_type, ["flds"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=ah_note_type)

    return ah_note_type


def note_types_with_template_changes_for_deck(ah_did: uuid.UUID) -> List[NotetypeId]:
    ids = []
    for mid in ankihub_db.note_types_for_ankihub_deck(ah_did):
        changed = False
        ah_note_type = note_type_without_ankihub_modifications(
            ankihub_db.note_type_dict(mid)
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


def new_fields_for_note_type(note_type: NotetypeDict) -> List[str]:
    field_names = aqt.mw.col.models.field_names(note_type)
    ankihub_field_names = ankihub_db.note_type_field_names(note_type["id"])
    new_fields = [name for name in field_names if name not in ankihub_field_names]
    return new_fields


def note_type_had_templates_added_or_removed(note_type: NotetypeDict) -> bool:
    ah_note_type = ankihub_db.note_type_dict(note_type["id"])

    if len(note_type["tmpls"]) != len(ah_note_type["tmpls"]):
        return True

    for anki_tmpl, ah_tmpl in zip(note_type["tmpls"], ah_note_type["tmpls"]):
        if anki_tmpl["name"] != ah_tmpl["name"]:
            return True

        # Ids were added in a recent Anki version, so we need to check if they exist
        if (
            anki_tmpl.get("id")
            and ah_tmpl.get("id")
            and anki_tmpl["id"] != ah_tmpl["id"]
        ):
            return True

    return False


def update_note_type_templates_and_styles(
    ah_did: uuid.UUID, note_type: NotetypeDict
) -> NotetypeDict:
    client = AddonAnkiHubClient()
    note_type = note_type_without_ankihub_modifications(note_type)
    ah_note_type = ankihub_db.note_type_dict(note_type["id"])

    ah_note_type["tmpls"] = note_type["tmpls"]
    ah_note_type["css"] = note_type["css"]

    ah_note_type = client.update_note_type(ah_did, ah_note_type, ["css", "tmpls"])
    ankihub_db.upsert_note_type(ankihub_did=ah_did, note_type=ah_note_type)

    return ah_note_type
