from typing import List, Dict

import anki
import aqt
from anki.models import NoteType
from aqt import mw

import constants


def note_type_contains_field(
    note_type: NoteType, field=constants.ANKIHUB_NOTE_TYPE_FIELD_NAME
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


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, editor: aqt.editor.Editor
) -> str:
    # TODO Henrik said this would have broke in 2.1.41:
    #  https://github.com/ankipalace/ankihub_addon/pull/1#pullrequestreview-597642485
    #  reevaluate and test.
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
        return js
    ord_ = note._fieldOrd(constants.ANKIHUB_NOTE_TYPE_FIELD_NAME)
    id_templs = ("f{}", "name{}")
    for id_templ in id_templs:
        id_ = id_templ.format(ord_)
        js += "\ndocument.getElementById('{}').style.display = 'none';".format(id_)
    return js
