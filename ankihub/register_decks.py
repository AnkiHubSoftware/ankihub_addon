"""Logic for the initial steps of registering local decks with collaborative
decks for both deck creators and deck users.
"""
import os
import pathlib
import tempfile
import uuid
from typing import Iterable

import aqt
from PyQt6.QtCore import qDebug
from anki.errors import NotFoundError
from anki.exporting import AnkiPackageExporter
from anki.models import NoteType
from anki.notes import Note
from aqt import mw
from aqt.utils import askUser, tooltip

from . import constants
from .ankihub_client import AnkiHubClient
from .utils import get_note_types_in_deck

DIR_PATH = os.path.dirname(os.path.abspath(__file__))


def populate_ankihub_id_fields(note_ids: Iterable[tuple[str, str]]) -> None:
    """Populate the AnkiHub ID field that was added to the Note Type by
    modify_note_type."""
    updated_notes = []
    for anki_id, ankihub_id, note_type_id in note_ids:
        try:
            note = mw.col.get_note(id=int(anki_id))
            note["AnkiHub ID"] = ankihub_id
            updated_notes.append(note)
        except NotFoundError:
            import aqt;aqt.debug()()
            note = Note(col=mw.col, model=note_type_id)
    mw.col.update_notes(updated_notes)


def modify_note_type(note_type: NoteType) -> None:
    """Adds the AnkiHub Field to the Note Type and modifies the template to
    display the field.
    """
    "Adds ankihub field. Adds link to ankihub in card template."
    mm = mw.col.models
    fields = note_type["flds"]
    field_names = [field["name"] for field in fields]
    if constants.ANKIHUB_NOTE_TYPE_FIELD_NAME in field_names:
        qDebug(f"{constants.ANKIHUB_NOTE_TYPE_FIELD_NAME} already exists.")
        return
    ankihub_field = mm.new_field(constants.ANKIHUB_NOTE_TYPE_FIELD_NAME)
    # Put our field last.
    ankihub_field["ord"] = len(fields)
    ankihub_field["size"] = 0
    mm.add_field(note_type, ankihub_field)
    # TODO Genericize this by creating a function that takes a template and
    #  returns a new template.
    link_html = "".join(
        (
            "\n{{#%s}}\n" % constants.ANKIHUB_NOTE_TYPE_FIELD_NAME,
            "<a class='ankihub' href='%s'>"
            % (
                constants.URL_VIEW_NOTE
                + "{{%s}}" % constants.ANKIHUB_NOTE_TYPE_FIELD_NAME
            ),
            "\nView Note on AnkiHub\n",
            "</a>",
            "\n{{/%s}}\n" % constants.ANKIHUB_NOTE_TYPE_FIELD_NAME,
        )
    )
    templates = note_type["tmpls"]
    for template in templates:
        template["afmt"] += link_html
    mm.save(note_type)


def modify_note_types(note_types: Iterable[str]):
    for note_type in note_types:
        note_type = mw.col.models.by_name(note_type)
        modify_note_type(note_type)
    # TODO Run add_id_fields


def upload_deck(did: int) -> None:
    """Upload the deck to AnkiHub."""
    deck_name = mw.col.decks.name(did)
    exporter = AnkiPackageExporter(mw.col)
    exporter.did = did
    exporter.includeMedia = False
    exporter.includeTags = True
    deck_uuid = uuid.uuid4()
    out_dir = pathlib.Path(tempfile.mkdtemp())
    out_file = str(out_dir / f"export-{deck_uuid}.apkg")
    exporter.exportInto(out_file)
    ankihub_client = AnkiHubClient()
    response = ankihub_client.upload_deck(f"{deck_name}.apkg")
    tooltip("Deck Uploaded to AnkiHub")
    return response


def _create_collaborative_deck(note_types, did):
    modify_note_types(note_types)
    upload_deck(did)


def create_collaborative_deck(did: int) -> None:
    model_ids = get_note_types_in_deck(did)
    note_types = [mw.col.models.get(model_id) for model_id in model_ids]
    names = ", ".join([note["name"] for note in note_types])
    response = askUser(
        "Uploading the deck to AnkiHub will modify the following note types, "
        f"and will require a full sync afterwards: {names}.  Continue?",
        title="AnkiHub",
    )
    if not response:
        tooltip("Cancelled Upload to AnkiHub")
        return
    mw.taskman.with_progress(
        task=lambda: _create_collaborative_deck(note_types, did),
        on_done=lambda future: tooltip("Deck Uploaded to AnkiHub"),
    )
