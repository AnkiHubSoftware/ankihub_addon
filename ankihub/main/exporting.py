"""Convert Anki notes to NoteInfo objects.
This is used for exporting notes from Anki to AnkiHub, e.g. when creating suggestions or creating new AnkiHub decks.
"""

import re
import uuid
from typing import List, Optional

from anki.models import NotetypeId
from anki.notes import Note

from ..ankihub_client import Field, NoteInfo
from ..db import ankihub_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .note_conversion import (
    get_fields_protected_by_tags,
    is_internal_tag,
    is_optional_tag,
)


def to_note_data(
    note: Note, set_new_id: bool = False, include_empty_fields: bool = False
) -> NoteInfo:
    """Convert an Anki note to a NoteInfo object.
    Tags and fields are altered (internal and optional tags are removed, ankihub id field is removed, etc.).
    Protected fields are removed.
    """

    if set_new_id:
        ah_nid = uuid.uuid4()
    else:
        ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)

    tags = _prepare_tags(note)
    fields = _prepare_fields(note, include_empty=include_empty_fields)

    return NoteInfo(
        ah_nid=ah_nid,
        anki_nid=note.id,
        mid=note.mid,
        fields=fields,
        tags=tags,
        guid=note.guid,
    )


def _prepare_fields(note: Note, include_empty: bool = False) -> List[Field]:
    note_type = ankihub_db.note_type_dict(note_type_id=NotetypeId(note.mid))
    if note_type is None:
        # When creating a deck the note type is not yet in the AnkiHub DB
        note_type = note.note_type()

    note_fields_dict = dict(note.items())
    fields_protected_by_tags = get_fields_protected_by_tags(note)

    result = []
    for field in note_type["flds"]:
        field_name = field["name"]
        value = note_fields_dict.get(field_name)
        if (
            field_name != ANKIHUB_NOTE_TYPE_FIELD_NAME
            and field_name not in fields_protected_by_tags
            and (include_empty or value)
        ):
            result.append(
                Field(
                    name=field_name,
                    value=_prepared_field_html(value),
                )
            )

    return result


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(note: Note) -> Optional[List[str]]:

    # Removing empty tags is necessary because notes have empty tags in the editor sometimes.
    # Stripping tags is necessary because Anki leaves whitespace at the end of tags sometimes.
    result = [
        tag.strip()
        for tag in note.tags
        if tag.strip() and not (is_internal_tag(tag) or is_optional_tag(tag))
    ]

    return result
