import anki
import aqt
from aqt import gui_hooks, mw
from aqt.qt import *
from aqt.studydeck import StudyDeck

from .consts import *
from .sync import prepare_to_upload_deck
from .service import Config
from .dialog import AnkiHubLogin


def on_upload() -> None:
    # This is a hack using the behaviour bool(iter([])) == True to remove 'add' button.
    diag = StudyDeck(mw, title="AnkiHub", accept="Upload", buttons=iter([]))  # type: ignore
    deck_name = diag.name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    prepare_to_upload_deck(did)


def show_sign_in_screen() -> None:
    global __window
    __window = AnkiHubLogin()

def signout() -> None:
    Config().signout()

def add_menu() -> None:
    ah_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ah_menu)
    if Config().isAuthenticated():
        upload_deck_action = QAction("Upload Deck", parent=ah_menu)
        qconnect(upload_deck_action.triggered, on_upload)
        ah_menu.addAction(upload_deck_action)
        sign_out_button = QAction("Sign out", mw)
        sign_out_button.triggered.connect(signout)
        ah_menu.addAction(sign_out_button)
    else:
        sign_in_button = QAction("Sign in", mw)
        sign_in_button.triggered.connect(show_sign_in_screen)
        ah_menu.addAction(sign_in_button)


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, editor: aqt.editor.Editor
) -> str:
    if FIELD_NAME not in note:
        return js
    ord = note._fieldOrd(FIELD_NAME)
    id_templs = ("f{}", "name{}")
    for id_templ in id_templs:
        id = id_templ.format(ord)
        js += "\ndocument.getElementById('{}').style.display = 'none';".format(id)
    return js


gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
add_menu()
