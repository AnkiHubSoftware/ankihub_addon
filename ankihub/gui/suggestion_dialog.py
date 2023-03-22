from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import List, Optional

import aqt
from anki.notes import Note
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRegularExpression,
    QRegularExpressionValidator,
    QSpacerItem,
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


class SourceType(Enum):
    AMBOSS = "AMBOSS"
    UWORLD = "UWorld"
    SOCIETY_GUIDELINES = "Society Guidelines"
    OTHER = "Other"


@dataclass
class SuggestionSource:
    source_type: SourceType
    source: str


@dataclass
class SuggestionMetadata:
    change_type: SuggestionType
    comment: str
    auto_accept: bool
    source: Optional[SuggestionSource] = None


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
            comment=_comment_with_source(suggestion_meta),
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
            comment=_comment_with_source(suggestion_meta),
        ),
        on_done=lambda future: _on_suggest_notes_in_bulk_done(future, parent),
        parent=parent,
    )


def _comment_with_source(suggestion_meta: SuggestionMetadata) -> str:
    result = suggestion_meta.comment
    if suggestion_meta.source:
        result += f"Source: {suggestion_meta.source.source_type.value} - {suggestion_meta.source.source}"

    return result


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


class SuggestionDialog(QDialog):
    silentlyClose = True

    validation_signal = pyqtSignal(bool)

    def __init__(self, is_new_note_suggestion: bool, is_for_ankihub_deck: bool) -> None:
        super().__init__()
        self._is_new_note_suggestion = is_new_note_suggestion
        self._is_for_ankihub_deck = is_for_ankihub_deck

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Note Suggestion(s)")

        self.layout_ = QVBoxLayout()
        self.setLayout(self.layout_)

        # Set up change type dropdown
        self.change_type_select = QComboBox()
        if not self._is_new_note_suggestion:
            self.change_type_select.addItems([x.value[1] for x in SuggestionType])
            label = QLabel("Change Type")
            self.layout_.addWidget(label)
            self.layout_.addWidget(self.change_type_select)
            qconnect(
                self.change_type_select.currentTextChanged,
                self._set_source_widget_visibility,
            )
            self.layout_.addSpacing(10)

        # Set up source widget in a group box (group box is for styling purposes)
        self.source_widget = SourceWidget()
        self.source_widget_group_box = QGroupBox("Source")
        self.layout_.addWidget(self.source_widget_group_box)
        self.source_widget_group_box_layout = QVBoxLayout()
        self.source_widget_group_box.setLayout(self.source_widget_group_box_layout)

        self.source_widget_group_box_layout.addWidget(self.source_widget)
        qconnect(self.source_widget.validation_signal, self._validate)
        self._set_source_widget_visibility()
        self.layout_.addSpacing(10)

        # Set up rationale field
        label = QLabel("Rationale for Change (Required)")
        self.layout_.addWidget(label)

        self.rationale_edit = QPlainTextEdit()
        self.layout_.addWidget(self.rationale_edit)

        def limit_length():
            while (
                len(self.rationale_edit.toPlainText())
                >= RATIONALE_FOR_CHANGE_MAX_LENGTH
            ):
                self.rationale_edit.textCursor().deletePreviousChar()

        qconnect(self.rationale_edit.textChanged, limit_length)
        qconnect(self.rationale_edit.textChanged, self._validate)

        self.layout_.addSpacing(10)

        # Set up "auto-accept" checkbox
        self.auto_accept_cb = QCheckBox("Submit without review (maintainers only).")
        self.layout_.addWidget(self.auto_accept_cb)

        # Set up button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)
        self.layout_.addWidget(self.button_box)

        self._set_submit_button_enabled_state(False)
        qconnect(self.validation_signal, self._set_submit_button_enabled_state)

    def run(self) -> Optional[SuggestionMetadata]:
        if not self.exec():
            return None

        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
            source=self.source_widget.suggestion_source()
            if self._source_needed()
            else None,
        )

    def _set_source_widget_visibility(self) -> None:
        if self._source_needed():
            self.source_widget_group_box.show()
        else:
            self.source_widget_group_box.hide()

    def _source_needed(self) -> bool:
        result = (
            self._change_type()
            in [
                SuggestionType.NEW_CONTENT,
                SuggestionType.UPDATED_CONTENT,
            ]
            and self._is_for_ankihub_deck
        )
        return result

    def _set_submit_button_enabled_state(self, enabled: bool) -> None:
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _validate(self) -> None:
        if self._is_valid():
            self.validation_signal.emit(True)
        else:
            self.validation_signal.emit(False)

    def _is_valid(self) -> bool:
        if len(self.rationale_edit.toPlainText().strip()) == 0:
            return False

        if not self.source_widget.is_valid():
            return False

        return True

    def _change_type(self) -> Optional[SuggestionType]:
        if self._is_new_note_suggestion:
            return None
        else:
            return next(
                x
                for x in SuggestionType
                if x.value[1] == self.change_type_select.currentText()
            )

    def _comment(self) -> str:
        return self.rationale_edit.toPlainText()

    def _auto_accept(self) -> bool:
        return self.auto_accept_cb.isChecked()


source_type_to_source_label = {
    SourceType.AMBOSS: "Link",
    SourceType.UWORLD: "UWorld Question ID",
    SourceType.SOCIETY_GUIDELINES: "Link",
    SourceType.OTHER: "",
}

UWORLD_STEP_OPTIONS = [
    "Step 1",
    "Step 2",
    "Step 3",
]


class SourceWidget(QWidget):

    validation_signal = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.layout_ = QVBoxLayout()
        self.setLayout(self.layout_)

        # Setup source type dropdown
        self.source_type_select = QComboBox()
        self.source_type_select.addItems([x.value for x in SourceType])
        self.layout_.addWidget(self.source_type_select)
        qconnect(
            self.source_type_select.currentTextChanged, self._on_source_type_change
        )
        self.layout_.addSpacing(10)

        # Setup UWorld step select
        self.uworld_step_select = QComboBox()
        self.uworld_step_select.addItems(UWORLD_STEP_OPTIONS)
        self.layout_.addWidget(self.uworld_step_select)
        self.space_after_uworld_step_select = QSpacerItem(0, 10)
        self.layout_.addSpacerItem(self.space_after_uworld_step_select)

        # Setup source field
        self.source_input_label = QLabel()
        self.layout_.addWidget(self.source_input_label)

        self.source_edit = QLineEdit()
        self.source_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r".+"))
        )
        qconnect(self.source_edit.textChanged, self._validate)
        self.layout_.addWidget(self.source_edit)

        # Set initial state
        self._on_source_type_change()

    def suggestion_source(self) -> SuggestionSource:
        source_type = self._source_type()
        source = self.source_edit.text()

        if source_type == SourceType.UWORLD:
            step = self.uworld_step_select.currentText()
            source = f"{step} {source}"

        return SuggestionSource(source_type=source_type, source=source)

    def is_valid(self) -> bool:
        return self.source_edit.hasAcceptableInput()

    def _validate(self) -> None:
        if not self.is_valid():
            self.validation_signal.emit(False)
        else:
            self.validation_signal.emit(True)

    def _on_source_type_change(self) -> None:
        self._refresh_source_input_label()

        if self._source_type() == SourceType.UWORLD:
            self.uworld_step_select.show()
            self.space_after_uworld_step_select.changeSize(0, 10)
            self.layout_.invalidate()
        else:
            self.uworld_step_select.hide()
            self.space_after_uworld_step_select.changeSize(0, 0)
            self.layout_.invalidate()

    def _refresh_source_input_label(self) -> None:
        source_type = self._source_type()
        text = source_type_to_source_label[source_type]

        self.source_input_label.setText(text)

        if not text:
            self.source_input_label.hide()
        else:
            self.source_input_label.show()

    def _source_type(self) -> SourceType:
        return SourceType(self.source_type_select.currentText())
