from typing import List, Dict

import anki
from anki.models import NoteType
from aqt import mw

from ankihub_addon.src.ankihub import constants


def note_type_contains_field(
        note_type: NoteType,
        field=constants.ANKIHUB_NOTE_TYPE_FIELD_NAME
) -> bool:
    """Check that a field is defined in the note type."""
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return True if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names else False


def get_note_types_in_deck(did: int) -> List[int]:
    """Returns list of note model ids in the given deck."""
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    dids = anki.utils.ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids} or odid in {dids}"
    )
    return mw.col.db.list(query)
