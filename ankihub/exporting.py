"""Convert Anki notes to NoteInfo objects"""

import re
import uuid
from typing import List

from anki.notes import Note

from .ankihub_client import Field, NoteInfo
from .note_conversion import get_fields_protected_by_tags, is_internal_tag
from .utils import ankihub_uuid_of_note


def to_note_data(note: Note, set_new_id: bool = False) -> NoteInfo:
    """Convert an Anki note to a NoteInfo object.
    Tags and fields are altered (internal tags are removed, ankihub id field is removed, etc.).
    Protected fields are removed.
    """

    if set_new_id:
        ankihub_note_uuid = uuid.uuid4()
    else:
        ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=False)

    tags = _prepare_tags(note.tags)
    fields = _prepare_fields(note)

    return NoteInfo(
        ankihub_note_uuid=ankihub_note_uuid,
        anki_nid=note.id,
        mid=note.mid,
        fields=fields,
        tags=tags,
        anki_guid=note.guid,
    )


def _prepare_fields(note: Note) -> List[Field]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    # Transform the field values
    prepared_field_vals = [_prepared_field_html(field) for field in field_vals]

    # Convert fields to Field objects, exclude fields that are protected by tags
    fields_protected_by_tags = get_fields_protected_by_tags(note)
    fields = [
        Field(name=field["name"], order=field["ord"], value=val)
        for field, val in zip(fields_metadata, prepared_field_vals)
        if field["name"] not in fields_protected_by_tags
    ]
    return fields


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(tags: List[str]):
    # removing empty tags is necessary because notes have empty tags in the editor sometimes
    return [tag for tag in tags if tag.strip() and not is_internal_tag(tag)]
