import anki
import aqt
from aqt import gui_hooks, mw
from aqt.qt import *
from aqt.studydeck import StudyDeck

from .consts import *
from .sync import prepare_to_upload_deck


def on_upload():
    # This is a hack using the behaviour bool(iter([])) == True to remove 'add' button.
    diag = StudyDeck(mw, title="AnkiHub", accept="Upload", buttons=iter([]))
    deck_name = diag.name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    prepare_to_upload_deck(did)


def add_menu():
    ah_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ah_menu)

    upload_deck_action = QAction("Upload Deck", parent=ah_menu)
    qconnect(upload_deck_action.triggered, on_upload)
    ah_menu.addAction(upload_deck_action)


def hide_ankihub_field_in_editor(js: str, note: anki.notes.Note, editor: aqt.editor.Editor) -> str:
    if not FIELD_NAME in note:
        return js
    ord = note._fieldOrd(FIELD_NAME)
    print(ord)
    id_templs = ("f{}", "name{}")
    for id_templ in id_templs:
        id = id_templ.format(ord)
        print(id)
        js += "\ndocument.getElementById('{}').style.display = 'none';".format(id)
    return js


gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
add_menu()
