from concurrent.futures import Future
from typing import List, Dict

import anki
from anki.models import NoteType
from anki.utils import ids2str
from aqt import mw
from aqt.utils import askUser, tooltip


from .consts import *


def get_note_types_in_deck(did: int) -> List[int]:
    "Returns list of note_type ids in deck."
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    # odid is the original did for cards in filtered decks
    return mw.col.db.list(
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        "WHERE did in {0} or odid in {0}".format(ids2str(dids))
    )


def add_id_fields(did: int):
    "Adds AnkiHub ID field to all notes in deck, *including* children decks."
    deck_name = mw.col.decks.name(did)
    nids = mw.col.find_notes(f'"deck:{deck_name}"')
    for nid in nids:
        note = mw.col.getNote(id=nid)
        if not note[FIELD_NAME]:
            note[FIELD_NAME] = str(nid)
        note.flush()


def has_ankihub_field(note_type: NoteType) -> bool:
    fields: List[Dict] = note_type["flds"]
    for field in fields:
        if field["name"] == FIELD_NAME:
            return True
    return False


def get_unprepared_note_types(mids: List[int]) -> List[NoteType]:
    "Returns list of note types that doesn't have ankihub field."
    mm = mw.col.models
    note_types_to_prepare = []
    for mid in mids:
        note_type = mm.get(mid)
        if not has_ankihub_field(note_type):
            note_types_to_prepare.append(note_type)

    return note_types_to_prepare


def prepare_note_types(note_types_to_prepare: List[NoteType]):
    "Adds ankihub field. Adds link to ankihub in card template."
    mm = mw.col.models
    for note_type in note_types_to_prepare:
        ankihub_field = mm.new_field(FIELD_NAME)
        # potential way to hide the field:
        # ankihub_field["size"] = 0
        mm.add_field(note_type, ankihub_field)
        modify_teplate(note_type)
        mm.save(note_type)


def modify_teplate(note_type: anki.models.NoteType):
    "Adds Ankihub link to card template"
    link_html = "".join(
        (
            "\n{{#%s}}\n" % FIELD_NAME,
            "<a class='ankihub' href='%s'>" % (URL_VIEW_NOTE + "{{%s}}" % FIELD_NAME),
            "\nView Note on AnkiHub\n",
            "</a>",
            "\n{{/%s}}\n" % FIELD_NAME,
        )
    )
    templates: List[Dict] = note_type["tmpls"]
    for template in templates:
        template["afmt"] += link_html


def prepare_to_upload_deck(did: int):
    mids = get_note_types_in_deck(did)
    # Currently only supports having a single cloze note type in deck
    assert len(mids) == 1
    assert mw.col.models.get(mids[0])["type"] == anki.consts.MODEL_CLOZE

    note_types_to_prepare = get_unprepared_note_types(mids)
    if len(note_types_to_prepare):
        res = askUser(
            "Uploading the deck to AnkiHub will modify your note type,"
            "and will require a full sync afterwards. Continue?",
            title="AnkiHub",
        )
        if not res:
            tooltip("Cancelled Upload to AnkiHub")
            return
        prepare_note_types(note_types_to_prepare)

    def on_done(fut: Future):
        upload_deck(did)

    mw.taskman.with_progress(lambda: add_id_fields(did), on_done)


def upload_deck(did: int):
    tooltip("Deck Uploaded to AnkiHub")
