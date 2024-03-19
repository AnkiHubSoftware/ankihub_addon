"""Contains code that is used for converting Anki Note objects to NoteInfo objects (export)
and from NoteInfo objects to Anki note objects (import)."""

import re
from typing import List

from anki.notes import Note

from .. import settings
from ..main.utils import is_tag_in_list
from .note_deletion import TAG_FOR_DELETED_NOTES

TAG_FOR_PROTECTING_FIELDS = "AnkiHub_Protect"
TAG_FOR_PROTECTING_ALL_FIELDS = f"{TAG_FOR_PROTECTING_FIELDS}::All"
TAG_FOR_OPTIONAL_TAGS = "AnkiHub_Optional"


# top-level tags that are only used by the add-on, but not by the web app
ADDON_INTERNAL_TAGS = [
    TAG_FOR_PROTECTING_FIELDS,
    TAG_FOR_DELETED_NOTES,
    "autoopen",  # Used by AnKing note types
]

# tags that are used internally by Anki and should not be deleted or appear in suggestions
ANKI_INTERNAL_TAGS = ["leech", "marked"]

INTERNAL_TAGS = ADDON_INTERNAL_TAGS + ANKI_INTERNAL_TAGS
INTERNAL_TAGS = [tag.lower() for tag in INTERNAL_TAGS]
INTERNAL_TAGS_REGEX = re.compile(
    "|".join(f"^{tag}($|::)" for tag in INTERNAL_TAGS), flags=re.IGNORECASE
)

OPTIONAL_TAG_REGEX = re.compile(
    f"^{TAG_FOR_OPTIONAL_TAGS.lower()}::", flags=re.IGNORECASE
)


def is_internal_tag(tag: str) -> bool:
    return bool(INTERNAL_TAGS_REGEX.match(tag))


def is_optional_tag(tag: str) -> bool:
    return bool(OPTIONAL_TAG_REGEX.match(tag))


def is_tag_for_group(tag: str, group_name: str) -> bool:
    return tag.lower().startswith(optional_tag_prefix_for_group(group_name).lower())


def optional_tag_prefix_for_group(group_name: str) -> str:
    return f"{TAG_FOR_OPTIONAL_TAGS}::{group_name.replace(' ', '_')}::"


def get_fields_protected_by_tags(note: Note) -> List[str]:
    result = _get_fields_protected_by_tags(note.tags, note.keys())
    return result


def _get_fields_protected_by_tags(tags: List[str], field_names: List[str]) -> List[str]:
    if is_tag_in_list(TAG_FOR_PROTECTING_ALL_FIELDS, tags):
        return [
            field_name
            for field_name in field_names
            if field_name != settings.ANKIHUB_NOTE_TYPE_FIELD_NAME
        ]

    tag_prefix = f"{TAG_FOR_PROTECTING_FIELDS}::".lower()
    field_names_from_tags = [
        tag[len(tag_prefix) :] for tag in tags if tag.lower().startswith(tag_prefix)
    ]

    # Both a field and the field with underscores replaced with spaces should be protected.
    # This makes it possible to protect fields with spaces in their name because tags cant contain spaces.
    standardized_field_names_from_tags = [
        field.replace("_", " ").lower() for field in field_names_from_tags
    ]
    standardized_field_names_from_note = [
        field.replace("_", " ").lower() for field in field_names
    ]

    result = [
        field
        for field, field_std in zip(field_names, standardized_field_names_from_note)
        if field_std in standardized_field_names_from_tags
    ]

    return result
