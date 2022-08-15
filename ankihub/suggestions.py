import uuid
from typing import Dict, List

from anki.notes import Note

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import ChangeTypes
from .sync import is_internal_tag
from .utils import ankihub_uuid_of_note


def suggest_note_update(note: Note, change_type: ChangeTypes, comment: str):

    ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=False)
    tags = _prepare_tags(note.tags)
    fields = _prepare_fields(note)

    client = AnkiHubClient()
    client.create_change_note_suggestion(
        ankihub_note_uuid=ankihub_note_uuid,
        fields=fields,
        tags=tags,
        change_type=change_type,
        comment=comment,
    )


def suggest_new_note(
    note: Note, change_type: ChangeTypes, comment: str, ankihub_deck_uuid: uuid.UUID
):

    ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=True)
    if not ankihub_note_uuid:
        ankihub_note_uuid = uuid.uuid4()

    tags = _prepare_tags(note.tags)
    fields = _prepare_fields(note)

    client = AnkiHubClient()
    client.create_new_note_suggestion(
        ankihub_deck_uuid=ankihub_deck_uuid,
        ankihub_note_uuid=ankihub_note_uuid,
        anki_note_id=note.id,
        fields=fields,
        tags=tags,
        change_type=change_type,
        note_type_name=note.note_type()["name"],
        anki_note_type_id=note.note_type()["id"],
        comment=comment,
    )


def _prepare_fields(note: Note) -> List[Dict]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    fields = [
        {"name": field["name"], "order": field["ord"], "value": val}
        for field, val in zip(fields_metadata, field_vals)
    ]
    return fields


def _prepare_tags(tags: List[str]):
    return [tag for tag in tags if not is_internal_tag(tag)]
