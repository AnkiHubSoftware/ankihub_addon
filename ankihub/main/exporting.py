"""Convert Anki notes to NoteInfo objects.
This is used for exporting notes from Anki to AnkiHub, e.g. when creating suggestions or creating new AnkiHub decks.
"""

import re
import uuid
from typing import Any, Dict, List, Optional

from anki.models import NotetypeDict, NotetypeId
from anki.notes import Note

from ..ankihub_client import Field, NoteInfo
from ..db import ankihub_db
from ..settings import ANKIHUB_NOTE_TYPE_FIELD_NAME
from .note_conversion import (
    get_fields_protected_by_tags,
    is_internal_tag,
    is_optional_tag,
)

# Sentinel marking "look the ah_nid up from the AnkiHub DB" — distinct from `ah_nid=None`,
# which is a real value meaning "this note has no AnkiHub counterpart yet" (new-note path).
AH_NID_LOOKUP = object()


def to_note_data(
    note: Note,
    set_new_id: bool = False,
    include_empty_fields: bool = False,
    include_protected_fields: bool = False,
    note_type_dict_cache: Optional[Dict[NotetypeId, NotetypeDict]] = None,
    ah_nid: Any = AH_NID_LOOKUP,
) -> NoteInfo:
    """Convert an Anki note to a NoteInfo object.
    Tags and fields are altered (internal and optional tags are removed, ankihub id field is removed).

    Personally-protected fields (those carrying `AnkiHub_Protect::FieldName` tags) are stripped
    by default so that paths without an explicit user filter — most importantly
    `deck_creation.create_ankihub_deck`'s upload — can't silently ship a user's local
    annotations to other subscribers. Opt in (`True`) only when the caller filters at the
    user's direction.

    Pass `note_type_dict_cache` (and/or `ah_nid`) to skip the per-call AnkiHub DB lookups
    in batched contexts where the caller has already resolved them.
    """

    if set_new_id:
        resolved_ah_nid: Optional[uuid.UUID] = uuid.uuid4()
    elif ah_nid is AH_NID_LOOKUP:
        resolved_ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    else:
        resolved_ah_nid = ah_nid

    tags = _prepare_tags(note)
    fields = _prepare_fields(
        note,
        include_empty=include_empty_fields,
        include_protected=include_protected_fields,
        note_type_dict_cache=note_type_dict_cache,
    )

    return NoteInfo(
        ah_nid=resolved_ah_nid,
        anki_nid=note.id,
        mid=note.mid,
        fields=fields,
        tags=tags,
        guid=note.guid,
    )


def _prepare_fields(
    note: Note,
    include_empty: bool = False,
    include_protected: bool = False,
    note_type_dict_cache: Optional[Dict[NotetypeId, NotetypeDict]] = None,
) -> List[Field]:
    mid = NotetypeId(note.mid)
    if note_type_dict_cache is not None and mid in note_type_dict_cache:
        note_type = note_type_dict_cache[mid]
    else:
        note_type = ankihub_db.note_type_dict(note_type_id=mid)
        if note_type is None:
            # When creating a deck the note type is not yet in the AnkiHub DB
            note_type = note.note_type()
        if note_type_dict_cache is not None:
            note_type_dict_cache[mid] = note_type

    note_fields_dict = dict(note.items())
    protected_to_strip = set() if include_protected else set(get_fields_protected_by_tags(note))

    result = []
    for field in note_type["flds"]:
        field_name = field["name"]
        value = note_fields_dict.get(field_name)
        if field_name == ANKIHUB_NOTE_TYPE_FIELD_NAME:
            continue
        if field_name in protected_to_strip:
            continue
        if not (include_empty or value):
            continue
        result.append(Field(name=field_name, value=_prepared_field_html(value)))

    return result


def _prepared_field_html(html: str) -> str:
    # Since Anki 2.1.54 data-editor-shrink attribute="True" is added to img tags when you double click them.
    # We don't want this attribute to appear in suggestions.
    result = re.sub(r" ?data-editor-shrink=['\"]true['\"]", "", html)
    return result


def _prepare_tags(note: Note) -> Optional[List[str]]:
    # Removing empty tags is necessary because notes have empty tags in the editor sometimes.
    # Stripping tags is necessary because Anki leaves whitespace at the end of tags sometimes.
    result = [tag.strip() for tag in note.tags if tag.strip() and not (is_internal_tag(tag) or is_optional_tag(tag))]

    return result
