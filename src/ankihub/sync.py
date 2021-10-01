from concurrent.futures import Future
from typing import List, Dict

import anki
from anki.models import NoteType
from anki.utils import ids2str
from aqt import mw
from aqt.utils import askUser, tooltip


from . import consts


def get_note_types_in_deck(did: int) -> List[int]:
    """Returns list of note_type ids in deck."""
    dids = [did]
    dids += [child[1] for child in mw.col.decks.children(did)]
    dids = ids2str(dids)
    # odid is the original did for cards in filtered decks
    query = (
        "SELECT DISTINCT mid FROM cards "
        "INNER JOIN notes ON cards.nid = notes.id "
        f"WHERE did in {dids} or odid in {dids}"
    )
    return mw.col.db.list(query)


def add_id_fields(did: int) -> None:
    "Adds AnkiHub ID field to all notes in deck, *including* children decks."
    deck_name = mw.col.decks.name(did)
    nids = mw.col.find_notes(f'"deck:{deck_name}"')
    for nid in nids:
        note = mw.col.getNote(id=nid)
        if not note[consts.ANKIHUB_NOTE_TYPE_FIELD_NAME]:
            note[consts.ANKIHUB_NOTE_TYPE_FIELD_NAME] = str(nid)
        note.flush()


def has_ankihub_field(note_type: NoteType) -> bool:
    fields: List[Dict] = note_type["flds"]
    field_names = [field["name"] for field in fields]
    return True if consts.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names else False


def modify_note_type(note_type: NoteType) -> None:
    "Adds ankihub field. Adds link to ankihub in card template."
    mm = mw.col.models
    ankihub_field = mm.new_field(consts.ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # potential way to hide the field:
    # ankihub_field["size"] = 0
    mm.add_field(note_type, ankihub_field)
    # TODO Use jinja template.
    link_html = "".join(
        (
            "\n{{#%s}}\n" % consts.ANKIHUB_NOTE_TYPE_FIELD_NAME,
            "<a class='ankihub' href='%s'>"
            % (consts.URL_VIEW_NOTE + "{{%s}}" % consts.ANKIHUB_NOTE_TYPE_FIELD_NAME),
            "\nView Note on AnkiHub\n",
            "</a>",
            "\n{{/%s}}\n" % consts.ANKIHUB_NOTE_TYPE_FIELD_NAME,
        )
    )
    templates: List[Dict] = note_type["tmpls"]
    # Can we always expect len(templates) == 1?
    for template in templates:
        template["afmt"] += link_html
    mm.save(note_type)


def prepare_to_upload_deck(did: int) -> None:
    mids = get_note_types_in_deck(did)
    # Currently only supports having a single cloze note type in deck
    assert len(mids) == 1
    assert mw.col.models.get(mids[0])["type"] == anki.consts.MODEL_CLOZE
    response = askUser(
        "Uploading the deck to AnkiHub will modify your note type, "
        "and will require a full sync afterwards.  Continue?",
        title="AnkiHub",
    )
    if not response:
        tooltip("Cancelled Upload to AnkiHub")
        return
    # TODO Get and pass in Anking Note Type
    # modify_note_type(1)

    def on_done(fut: Future) -> None:
        upload_deck(did)

    mw.taskman.with_progress(lambda: add_id_fields(did), on_done)


def upload_deck(did: int) -> None:
    tooltip("Deck Uploaded to AnkiHub")
