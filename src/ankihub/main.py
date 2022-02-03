import anki
import aqt
from aqt import gui_hooks, mw
from aqt.qt import QMenu, QAction, qconnect
from aqt.studydeck import StudyDeck

from . import consts
from .sync import prepare_to_upload_deck


def on_upload() -> None:
    # This is a hack using the behaviour bool(iter([])) == True to remove 'add' button.
    diag = StudyDeck(mw, title="AnkiHub", accept="Upload", buttons=iter([]))  # type: ignore
    deck_name = diag.name
    if not deck_name:
        return
    did = mw.col.decks.id(deck_name)
    prepare_to_upload_deck(did)


def add_menu() -> None:
    ah_menu = QMenu("&AnkiHub", parent=mw)
    mw.form.menubar.addMenu(ah_menu)

    upload_deck_action = QAction("Upload Deck", parent=ah_menu)
    qconnect(upload_deck_action.triggered, on_upload)
    ah_menu.addAction(upload_deck_action)


def hide_ankihub_field_in_editor(
    js: str, note: anki.notes.Note, editor: aqt.editor.Editor
) -> str:
    # TODO Henrik said this would have broke in 2.1.41:
    #  https://github.com/ankipalace/ankihub_addon/pull/1#pullrequestreview-597642485
    #  reevaluate and test.
    if consts.ANKIHUB_NOTE_TYPE_FIELD_NAME not in note:
        return js
    ord_ = note._fieldOrd(consts.ANKIHUB_NOTE_TYPE_FIELD_NAME)
    id_templs = ("f{}", "name{}")
    for id_templ in id_templs:
        id_ = id_templ.format(ord_)
        js += "\ndocument.getElementById('{}').style.display = 'none';".format(id_)
    return js


gui_hooks.editor_will_load_note.append(hide_ankihub_field_in_editor)
add_menu()
