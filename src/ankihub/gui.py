from aqt import gui_hooks, mw
from aqt.qt import QMenu, QAction, qconnect
from aqt.studydeck import StudyDeck

from .register_decks import create_shared_deck
from .utils import hide_ankihub_field_in_editor


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


gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
add_ankihub_menu()
