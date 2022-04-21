from typing import Dict, List

import anki
import aqt
from PyQt6.QtCore import qDebug
from anki.errors import NotFoundError
from anki.models import NoteType
from aqt import mw

from anki.notes import Note
from . import constants


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


def create_note(note, anki_id):
    # TODO Add to an appropriate deck.
    mw.col.add_note(note, 1)
    # Swap out the note id that Anki assigns to the new note with our own id.
    sql = (
        f"UPDATE notes SET id={anki_id} WHERE id={note.id};"
        f"UPDATE cards SET nid={anki_id} WHERE nid={note.id};"
    )
    mw.col.db.execute(sql)
    qDebug(f"Created note: {note.anki_id}")


def update_or_create_note(anki_id, ankihub_id, fields, tags, note_type):
    try:
        note = mw.col.get_note(id=int(anki_id))
    except NotFoundError:
        note_type = mw.col.models.by_name(note_type)
        note = Note(col=mw.col, model=note_type)
        create_note(note, anki_id)

    note["AnkiHub ID"] = str(ankihub_id)
    note.tags = [str(tag) for tag in tags]
    for field in fields:
        note[field["name"]] = field["value"]
    mw.col.update_notes([note])
    qDebug(f"Updated note {anki_id}")
