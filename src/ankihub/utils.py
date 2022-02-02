from typing import List, Dict

from anki.models import NoteType

from ankihub_addon.src.ankihub import consts


def note_type_contains_field(
        note_type: NoteType,
        field=consts.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return True if consts.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names else False
