from aqt import mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.studydeck import StudyDeck

from .dialog import AnkiHubLogin
from .register_decks import create_shared_deck


def create_shared_deck_action() -> None:
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
    create_shared_deck(did)


def add_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    ankihub_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ankihub_menu)
    _create_shared_deck_action = QAction("Upload Deck", parent=ankihub_menu)
    qconnect(_create_shared_deck_action.triggered, create_shared_deck_action)
    ankihub_menu.addAction(_create_shared_deck_action)
    sign_in_button = QAction("Sign in", mw)
    sign_in_button.triggered.connect(show_sign_in_screen)
    ankihub_menu.addAction(sign_in_button)


def show_sign_in_screen() -> None:
    # TODO Figure out if this is necessary
    global __window
    __window = AnkiHubLogin()
