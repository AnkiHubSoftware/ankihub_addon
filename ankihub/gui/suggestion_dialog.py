from concurrent.futures import Future
from dataclasses import dataclass
from pprint import pformat
from typing import List, Optional

import aqt
from anki.notes import Note
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QSize,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import showInfo, showText, tooltip

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError, SuggestionType
from ..db import ankihub_db
from ..settings import RATIONALE_FOR_CHANGE_MAX_LENGTH
from ..suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    BulkNoteSuggestionsResult,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)


def open_suggestion_dialog_for_note(
    note: Note,
) -> bool:
    """Opens a dialog for creating a note suggestion for the given note.
    Returns True if the suggestion was created, False if the user cancelled the dialog,
    or if the note has no changes (and therefore no suggestion was created).
    The note has to saved to the Anki collection before calling this function.
    May change the notes contents (e.g. by renaming media files) and therefore the
    note might need to be reloaded after this function is called.
    """

    assert ankihub_db.is_ankihub_note_type(
        note.mid
    ), f"Note type {note.mid} is not associated with an AnkiHub deck."

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)

    suggestion_meta = SuggestionDialog(is_new_note_suggestion=ah_nid is None).run()
    if suggestion_meta is None:
        return False

    if ah_nid:
        if suggest_note_update(
            note=note,
            change_type=suggestion_meta.change_type,
            comment=suggestion_meta.comment,
            auto_accept=suggestion_meta.auto_accept,
        ):
            tooltip("Submitted suggestion to AnkiHub.")
            return True
        else:
            tooltip("No changes. Try syncing with AnkiHub first.")
            return False
    else:
        ah_did = ankihub_db.ankihub_did_for_note_type(note.mid)
        suggest_new_note(
            note=note,
            ankihub_did=ah_did,
            comment=suggestion_meta.comment,
            auto_accept=suggestion_meta.auto_accept,
        )
        tooltip("Submitted suggestion to AnkiHub.")
        return True


def open_suggestion_dialog_for_bulk_suggestion(
    notes: List[Note], parent: QWidget
) -> None:
    """Opens a dialog for creating a bulk suggestion for the given notes.
    The suggestions are created in the background and the on_done callback is called
    when the suggestions have been created."""

    mids = set(note.mid for note in notes)
    if not all(ankihub_db.is_ankihub_note_type(mid) for mid in mids):
        showInfo(
            "Some of the notes you selected are not of a note type that is known by AnkiHub."
        )
        return

    if len(notes) > 500:
        msg = "Please select less than 500 notes at a time for bulk suggestions.<br>"
        showInfo(msg, parent=parent)
        return

    ah_dids = set(ankihub_db.ankihub_did_for_note_type(mid) for mid in mids)
    if len(ah_dids) > 1:
        msg = "You can only bulk suggest notes from one AnkiHub deck at a time.<br>"
        showInfo(msg, parent=parent)
        return

    suggestion_meta = SuggestionDialog(is_new_note_suggestion=False).run()
    if not suggestion_meta:
        return

    aqt.mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            notes,
            auto_accept=suggestion_meta.auto_accept,
            change_type=suggestion_meta.change_type,
            comment=suggestion_meta.comment,
        ),
        on_done=lambda future: _on_suggest_notes_in_bulk_done(future, parent),
        parent=parent,
    )


def _on_suggest_notes_in_bulk_done(future: Future, parent: QWidget) -> None:
    try:
        suggestions_result: BulkNoteSuggestionsResult = future.result()
    except AnkiHubRequestError as e:
        if e.response.status_code != 403:
            raise e

        msg = (
            "You are not allowed to create suggestion for all selected notes.<br>"
            "Are you subscribed to the AnkiHub deck(s) these notes are from?<br><br>"
            "You can only submit changes without a review if you are an owner or maintainer of the deck."
        )
        showInfo(msg, parent=parent)
        return

    LOGGER.info("Created note suggestions in bulk.")
    LOGGER.info(f"errors_by_nid:\n{pformat(suggestions_result.errors_by_nid)}")

    msg_about_created_suggestions = (
        f"Submitted {suggestions_result.change_note_suggestions_count} change note suggestion(s).\n"
        f"Submitted {suggestions_result.new_note_suggestions_count} new note suggestion(s) to.\n\n\n"
    )

    notes_without_changes = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if ANKIHUB_NO_CHANGE_ERROR in str(errors)
    ]
    msg_about_failed_suggestions = (
        (
            f"Failed to submit suggestions for {len(suggestions_result.errors_by_nid)} note(s).\n"
            "All notes with failed suggestions:\n"
            f'{", ".join(str(nid) for nid in suggestions_result.errors_by_nid.keys())}\n\n'
            f"Notes without changes ({len(notes_without_changes)}):\n"
            f'{", ".join(str(nid) for nid in notes_without_changes)}\n'
        )
        if suggestions_result.errors_by_nid
        else ""
    )

    msg = msg_about_created_suggestions + msg_about_failed_suggestions
    showText(msg, parent=parent)


@dataclass
class SuggestionMetadata:
    comment: str
    auto_accept: bool
    change_type: SuggestionType


class SuggestionDialog(QDialog):
    silentlyClose = True

    def __init__(self, is_new_note_suggestion: bool) -> None:
        super().__init__()
        self._is_new_note_suggestion = is_new_note_suggestion

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Note Suggestion(s)")

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.select = select = CustomListWidget()
        select.addItems([x.value[1] for x in SuggestionType])
        select.setCurrentRow(0)

        if not self._is_new_note_suggestion:
            # change type select
            label = QLabel("Change Type")
            layout.addWidget(label)
            layout.addWidget(select)

        # comment field
        label = QLabel("Rationale for Change (Required)")
        layout.addWidget(label)

        self.edit = edit = QPlainTextEdit()

        def limit_length():
            while len(edit.toPlainText()) >= RATIONALE_FOR_CHANGE_MAX_LENGTH:
                edit.textCursor().deletePreviousChar()

        edit.textChanged.connect(limit_length)  # type: ignore
        layout.addWidget(edit)

        # "auto-accept" checkbox
        self.auto_accept_cb = QCheckBox("Submit without review (maintainers only).")
        layout.addWidget(self.auto_accept_cb)

        # button box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(button_box.accepted, self.accept)
        layout.addWidget(button_box)

        # disable save button when rationale for change field is empty
        button_box.setDisabled(True)

        def toggle_save_button_disabled_state():
            if len(edit.toPlainText().strip()) == 0:
                button_box.setDisabled(True)
            else:
                button_box.setDisabled(False)

        edit.textChanged.connect(toggle_save_button_disabled_state)  # type: ignore

    def run(self) -> Optional[SuggestionMetadata]:
        if not self.exec():
            return None

        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
        )

    def _comment(self) -> str:
        return self.edit.toPlainText()

    def _change_type(self) -> Optional[SuggestionType]:
        if self._is_new_note_suggestion:
            return None
        else:
            return next(
                x
                for x in SuggestionType
                if x.value[1] == self.select.currentItem().text()
            )

    def _auto_accept(self) -> bool:
        return self.auto_accept_cb.isChecked()


class CustomListWidget(QListWidget):
    def sizeHint(self) -> QSize:
        # adjusts height to content
        size = QSize()
        size.setHeight(self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth())
        return size
