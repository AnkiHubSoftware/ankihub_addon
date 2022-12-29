"""Convert Anki notes to NoteInfo objects"""
import re
import uuid
from typing import List, Optional

from anki.notes import Note

from .ankihub_client import Field, NoteInfo
from .db import ankihub_db
from .note_conversion import get_fields_protected_by_tags, is_internal_tag
from .utils import ankihub_uuid_of_note


def to_note_data(note: Note, set_new_id: bool = False, diff: bool = False) -> NoteInfo:
    """Convert an Anki note to a NoteInfo object.
    Tags and fields are altered (internal tags are removed, ankihub id field is removed, etc.).
    Protected fields are removed.
    If diff is True then only the fields that were changed since the last sync will be included.
    """

    if set_new_id:
        ankihub_note_uuid = uuid.uuid4()
    else:
        ankihub_note_uuid = ankihub_uuid_of_note(note, ignore_invalid=False)

    tags = _prepare_tags(note, diff=diff)
    fields = _prepare_fields(note, diff=diff)

    return NoteInfo(
        ankihub_note_uuid=ankihub_note_uuid,
        anki_nid=note.id,
        mid=note.mid,
        fields=fields,
        tags=tags,
        guid=note.guid,
    )


def _prepare_fields(note: Note, diff: bool) -> List[Field]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    result = [
        Field(name=field_metadata["name"], order=field_metadata["ord"], value=val)
        for field_metadata, val in zip(fields_metadata, field_vals)
    ]

    if diff:
        result = _fields_that_changed(note, result)

    for field in result:
        field.value = _prepared_field_html(field.value)

    fields_protected_by_tags = get_fields_protected_by_tags(note)
    result = [field for field in result if field.name not in fields_protected_by_tags]
    return result


def _fields_that_changed(note: Note, fields: List[Field]) -> List[Field]:
    note_data_from_ah = ankihub_db.note_data(note.id)
    result = [
        field_anki
        for field_anki, field_ah in zip(fields, note_data_from_ah.fields)
        if field_anki.value != field_ah.value
    ]
    return result


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(note: Note, diff: bool) -> Optional[List[str]]:
    # returns None if diff=True and the tags didn't change since the last sync

    # removing empty tags is necessary because notes have empty tags in the editor sometimes
    result = [tag for tag in note.tags if tag.strip() and not is_internal_tag(tag)]

    if diff:
        note_data_from_ah = ankihub_db.note_data(note.id)
        if set(note_data_from_ah.tags) == set(result):
            return None

    return result
