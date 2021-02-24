from aqt import mw
from aqt.qt import *
from aqt.studydeck import StudyDeck

from .sync import upload_deck


def on_upload():
    deck_name = StudyDeck(mw, title="ankihub").name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    upload_deck(did)


def add_menu():
    ah_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ah_menu)

    upload_deck_action = QAction("Upload Deck", parent=ah_menu)
    qconnect(upload_deck_action.triggered, on_upload)
    ah_menu.addAction(upload_deck_action)


add_menu()
