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
    pyqtSignal,
    qconnect,
)
from aqt.utils import showInfo, showText, tooltip

from .. import LOGGER
from ..ankihub_client import AnkiHubRequestError, SuggestionType
from ..db import ankihub_db
from ..settings import ANKING_DECK_ID, RATIONALE_FOR_CHANGE_MAX_LENGTH
from ..suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    BulkNoteSuggestionsResult,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)


def open_suggestion_dialog_for_note(note: Note, parent: QWidget) -> None:
    """Opens a dialog for creating a note suggestion for the given note.
    The note has to be present in the Anki collection before calling this function.
    May change the notes contents (e.g. by renaming media files) and therefore the
    note might need to be reloaded after this function is called.
    """

    assert ankihub_db.is_ankihub_note_type(
        note.mid
    ), f"Note type {note.mid} is not associated with an AnkiHub deck."

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    ah_did = ankihub_db.ankihub_did_for_note_type(note.mid)

    suggestion_meta = SuggestionDialog(
        is_new_note_suggestion=ah_nid is None,
        is_for_ankihub_deck=ah_did == ANKING_DECK_ID,
    ).run()
    if suggestion_meta is None:
        return

    if ah_nid:
        if suggest_note_update(
            note=note,
            change_type=suggestion_meta.change_type,
            comment=suggestion_meta.comment,
            auto_accept=suggestion_meta.auto_accept,
        ):
            tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        else:
            tooltip("No changes. Try syncing with AnkiHub first.", parent=parent)
    else:
        suggest_new_note(
            note=note,
            ankihub_did=ah_did,
            comment=suggestion_meta.comment,
            auto_accept=suggestion_meta.auto_accept,
        )
        tooltip("Submitted suggestion to AnkiHub.", parent=parent)


def open_suggestion_dialog_for_bulk_suggestion(
    notes: List[Note], parent: QWidget
) -> None:
    """Opens a dialog for creating a bulk suggestion for the given notes.
    The notes have to be present in the Anki collection before calling this function.
    May change the notes contents (e.g. by renaming media files) and therefore the
    notes might need to be reloaded after this function is called."""

    mids = set(note.mid for note in notes)
    assert (
        ankihub_db.is_ankihub_note_type(mid) for mid in mids
    ), "Some of the note types of the notes are not associated with an AnkiHub deck."

    ah_dids = set(ankihub_db.ankihub_did_for_note_type(mid) for mid in mids)
    assert len(ah_dids) == 1, "All notes have to be from the same AnkiHub deck."

    ah_did = ah_dids.pop()

    suggestion_meta = SuggestionDialog(
        is_new_note_suggestion=False, is_for_ankihub_deck=ah_did == ANKING_DECK_ID
    ).run()
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

    validation_slot = pyqtSignal(bool)

    def __init__(self, is_new_note_suggestion: bool, is_for_ankihub_deck: bool) -> None:
        super().__init__()
        self._is_new_note_suggestion = is_new_note_suggestion
        self._is_for_ankihub_deck = is_for_ankihub_deck

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Note Suggestion(s)")

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.select = CustomListWidget()
        self.select.addItems([x.value[1] for x in SuggestionType])
        qconnect(self.select.selectionModel().selectionChanged, self._validate)

        if not self._is_new_note_suggestion:
            # change type select
            label = QLabel("Change Type")
            layout.addWidget(label)
            layout.addWidget(self.select)

        # comment field
        label = QLabel("Rationale for Change (Required)")
        layout.addWidget(label)

        self.edit = QPlainTextEdit()

        def limit_length():
            while len(self.edit.toPlainText()) >= RATIONALE_FOR_CHANGE_MAX_LENGTH:
                self.edit.textCursor().deletePreviousChar()

        qconnect(self.edit.textChanged, limit_length)
        qconnect(self.edit.textChanged, self._validate)

        layout.addWidget(self.edit)

        # "auto-accept" checkbox
        self.auto_accept_cb = QCheckBox("Submit without review (maintainers only).")
        layout.addWidget(self.auto_accept_cb)

        # button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)
        layout.addWidget(self.button_box)

        # Disable submit button until validation passes
        self._set_submit_button_enabled_state(False)
        qconnect(self.validation_slot, self._set_submit_button_enabled_state)

        # Set initial focus in change type select to the first option, if the suggestion is not for the AnKing deck.
        if not self._is_for_ankihub_deck:
            self.select.setCurrentRow(0)

    def run(self) -> Optional[SuggestionMetadata]:
        if not self.exec():
            return None

        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
        )

    def _set_submit_button_enabled_state(self, enabled: bool) -> None:
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _validate(self) -> None:
        if len(self.edit.toPlainText().strip()) == 0:
            self.validation_slot.emit(False)
            return

        if not self._is_new_note_suggestion and not self.select.selectedItems():
            self.validation_slot.emit(False)
            return

        self.validation_slot.emit(True)

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
