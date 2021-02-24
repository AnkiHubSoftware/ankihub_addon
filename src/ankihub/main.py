from aqt import mw
from aqt.qt import *

from .sync import upload_deck


def on_upload():
    decks = mw.col.decks.all_names_and_ids(skip_empty_default=True)
    deck_names = [deck.name for deck in decks]
    window = QWidget(mw)
    deck_name, ok = QInputDialog.getItem(
        window, "AnkiHub", "Which deck do you want to upload?", deck_names, 0, False)
    if ok and deck_name:
        idx = deck_names.index(deck_name)
        did = decks[idx].id
        upload_deck(did)


def add_menu():
    ah_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ah_menu)

    upload_deck_action = QAction("Upload Deck", parent=ah_menu)
    qconnect(upload_deck_action.triggered, on_upload)
    ah_menu.addAction(upload_deck_action)


add_menu()
