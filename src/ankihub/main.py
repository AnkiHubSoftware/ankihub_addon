import anki
import aqt
from aqt import gui_hooks, mw
from aqt.qt import QMenu, QAction, qconnect
from aqt.studydeck import StudyDeck

from . import constants
from .register_decks import upload_deck


def on_upload() -> None:
    diag = StudyDeck(
        mw,
        title="AnkiHub",
        accept="Upload",
        # Removes the "Add" button
        buttons=[],
    )
    deck_name = diag.name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    upload_deck(did)


def add_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    upload_deck_action = QAction("Upload Deck", parent=ankihub_menu)
    qconnect(upload_deck_action.triggered, on_upload)
    ankihub_menu.addAction(upload_deck_action)


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


gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
add_ankihub_menu()
