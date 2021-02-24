from typing import List, Dict

import anki
from aqt import mw
from anki.utils import ids2str

from .consts import *


def get_note_types_in_deck(did: int) -> List[int]:
    "Returns list of note_type ids in deck."
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    # odid is the original did for cards in filtered decks
    return mw.col.db.list("SELECT DISTINCT mid FROM cards "
                          "INNER JOIN notes ON cards.nid = notes.id "
                          "WHERE did in {0} or odid in {0}".format(ids2str(dids)))


def prepare_note_type(mid: int):
    "Add ankihub field if it doesn't exist in note type. Modify template."
    mm = mw.col.models
    note_type: anki.models.NoteType = mm.get(mid)
    fields: List[Dict] = note_type["flds"]

    for field in fields:
        if field["name"] == FIELD_NAME:
            return

    ankihub_field = mm.new_field(FIELD_NAME)
    mm.add_field(note_type, ankihub_field)
    mm.save(note_type)
    modify_teplate(mid)


def modify_teplate(mid: int):
    "Adds Ankihub link to card template"
    link_html = ''.join(("\n{{#%s}}\n" % FIELD_NAME,
                         "<a class='ankihub' href='%s'>" % (
                             URL_VIEW_NOTE + "{{%s}}" % FIELD_NAME),
                         "\nView Note on AnkiHub\n",
                         "</a>",
                         "\n{{/%s}}\n" % FIELD_NAME))

    mm = mw.col.models
    model = mm.get(mid)
    templates: List[Dict] = model["tmpls"]
    for template in templates:
        template['qfmt'] += link_html
        template['afmt'] += link_html
    mm.save(model)


def upload_deck(did: int):
    mids = get_note_types_in_deck(did)
    assert len(mids) == 1  # Currently only supports having one note type

    for mid in mids:
        prepare_note_type(mid)
