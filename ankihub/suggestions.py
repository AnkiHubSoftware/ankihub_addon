import re
import uuid
from typing import Dict, List

from anki.notes import Note

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import SuggestionType
from .sync import is_internal_tag
from .utils import ankihub_uuid_of_note


def suggest_note_update(note: Note, change_type: SuggestionType, comment: str):
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


def suggest_new_note(note: Note, comment: str, ankihub_deck_uuid: uuid.UUID):
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
        note_type_name=note.note_type()["name"],
        anki_note_type_id=note.note_type()["id"],
        comment=comment,
    )


def _prepare_fields(note: Note) -> List[Dict]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    prepared_field_vals = [_prepared_field_val(field) for field in field_vals]
    fields = [
        {"name": field["name"], "order": field["ord"], "value": val}
        for field, val in zip(fields_metadata, prepared_field_vals)
    ]
    return fields


def _prepared_field_val(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(tags: List[str]):
    # removing empty tags is necessary because notes have empty tags in the editor sometimes
    return [tag for tag in tags if tag.strip() and not is_internal_tag(tag)]
