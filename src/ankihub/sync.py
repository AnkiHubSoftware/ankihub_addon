from concurrent.futures import Future
from typing import List

import anki
from anki.models import NoteType
import anki.utils
from aqt import mw
from aqt.utils import askUser, tooltip


from . import consts


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


def populate_ankihub_id_fields(did: int) -> None:
    """Populate the AnkiHub ID field that was added to the Note Type by
    modify_note_type."""
    # TODO Get the lest of AnkiHub IDs from AnkiHub.
    # TODO This should operate on a mapping between AnkiHub IDs and Anki Note IDs.
    deck_name = mw.col.decks.name(did)
    note_ids = mw.col.find_notes(f'"deck:{deck_name}"')
    for nid in note_ids:
        note = mw.col.getNote(id=nid)
        if consts.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note.fields:
            # Log error
            continue
        note.flush()


def modify_note_type(note_type: NoteType) -> None:
    """Adds the AnkiHub Field to the Note Type and modifies the template to
    display the field.
    """
    "Adds ankihub field. Adds link to ankihub in card template."
    mm = mw.col.models
    ankihub_field = mm.new_field(consts.ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # potential way to hide the field:
    # ankihub_field["size"] = 0
    mm.add_field(note_type, ankihub_field)
    # TODO Use cleaner templating strategy.
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
    templates = note_type["tmpls"]
    # Can we always expect len(templates) == 1?
    for template in templates:
        template["afmt"] += link_html
    mm.save(note_type)


def prepare_to_upload_deck(did: int) -> None:
    model_ids = get_note_types_in_deck(did)
    try:
        assert len(model_ids) == 1
        assert mw.col.models.get(model_ids[0])["type"] == anki.consts.MODEL_CLOZE
    except AssertionError:
        # TODO Is this even true?  I can't remember if what the reason for this would be.
        #  Make sure we come back to this.
        tooltip("AnkiHub only supports collaborating on decks with a single "
                "note type.")
    note_type = model_ids.pop()
    response = askUser(
        "Uploading the deck to AnkiHub will modify your note type, "
        "and will require a full sync afterwards.  Continue?",
        title="AnkiHub",
    )
    if not response:
        tooltip("Cancelled Upload to AnkiHub")
    modify_note_type(note_type)
    # TODO Run add_id_fields

    def on_done(fut: Future) -> None:
        upload_deck(did)

    mw.taskman.with_progress(lambda: populate_ankihub_id_fields(did), on_done)


def upload_deck(did: int) -> None:
    tooltip("Deck Uploaded to AnkiHub")
