"""Convert Anki notes to NoteInfo objects.
This is used for exporting notes from Anki to AnkiHub, e.g. when creating suggestions or creating new AnkiHub decks."""
import re
import uuid
from typing import List, Optional

from anki.notes import Note

from .ankihub_client import Field, NoteInfo
from .db import ankihub_db
from .note_conversion import (
    get_fields_protected_by_tags,
    is_internal_tag,
    is_optional_tag,
)


def to_note_data(note: Note, set_new_id: bool = False) -> NoteInfo:
    """Convert an Anki note to a NoteInfo object.
    Tags and fields are altered (internal and optional tags are removed, ankihub id field is removed, etc.).
    Protected fields are removed.
    """

    if set_new_id:
        ankihub_note_uuid = uuid.uuid4()
    else:
        ankihub_note_uuid = ankihub_db.ankihub_nid_for_anki_nid(note.id)

    tags = _prepare_tags(note)
    fields = _prepare_fields(note)

    return NoteInfo(
        ankihub_note_uuid=ankihub_note_uuid,
        anki_nid=note.id,
        mid=note.mid,
        fields=fields,
        tags=tags,
        guid=note.guid,
    )


def _prepare_fields(note: Note) -> List[Field]:

    # Exclude the AnkiHub ID field since we don't want to expose this as an
    # editable field in AnkiHub suggestion forms.
    field_vals = list(note.fields[:-1])
    fields_metadata = note.note_type()["flds"][:-1]

    result = [
        Field(name=field_metadata["name"], order=field_metadata["ord"], value=val)
        for field_metadata, val in zip(fields_metadata, field_vals)
    ]

    for field in result:
        field.value = _prepared_field_html(field.value)

    fields_protected_by_tags = get_fields_protected_by_tags(note)
    result = [field for field in result if field.name not in fields_protected_by_tags]
    return result


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(note: Note) -> Optional[List[str]]:

    # removing empty tags is necessary because notes have empty tags in the editor sometimes
    result = [
        tag
        for tag in note.tags
        if tag.strip() and not (is_internal_tag(tag) or is_optional_tag(tag))
    ]

    return result
