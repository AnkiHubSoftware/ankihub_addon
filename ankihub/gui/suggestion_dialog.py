"""Dialog for creating a suggestion for a note or a bulk suggestion for multiple notes."""
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import Collection, Optional

import aqt
from anki.notes import Note, NoteId
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
from aqt.utils import show_info, showText

from .. import LOGGER
from ..ankihub_client import (
    AnkiHubHTTPError,
    SuggestionType,
    get_media_names_from_note_info,
)
from ..db import ankihub_db
from ..main.exporting import to_note_data
from ..main.suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    BulkNoteSuggestionsResult,
    get_anki_nid_to_possible_ah_dids_dict,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)
from ..settings import ANKING_DECK_ID, RATIONALE_FOR_CHANGE_MAX_LENGTH
from .media_sync import media_sync
from .utils import choose_ankihub_deck, show_error_dialog, show_tooltip


class SourceType(Enum):
    AMBOSS = "AMBOSS"
    UWORLD = "UWorld"
    SOCIETY_GUIDELINES = "Society Guidelines"
    OTHER = "Other"


@dataclass
class SuggestionSource:
    source_type: SourceType
    source_text: str


@dataclass
class SuggestionMetadata:
    comment: str
    auto_accept: bool = False
    change_type: Optional[SuggestionType] = None
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

    ah_did = _determine_ah_did_for_nids_to_be_suggested([note.id], parent)
    if not ah_did:
        LOGGER.info("Suggestion cancelled.")
        return

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    suggestion_meta = SuggestionDialog(
        is_new_note_suggestion=ah_nid is None,
        is_for_anking_deck=ah_did == ANKING_DECK_ID,
        added_new_media=_added_new_media(note),
    ).run()
    if suggestion_meta is None:
        return

    if ah_nid:
        if suggest_note_update(
            note=note,
            change_type=suggestion_meta.change_type,
            comment=_comment_with_source(suggestion_meta),
            media_upload_cb=media_sync.start_media_upload,
            auto_accept=suggestion_meta.auto_accept,
        ):
            show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        else:
            show_tooltip("No changes. Try syncing with AnkiHub first.", parent=parent)
    else:
        suggest_new_note(
            note=note,
            ankihub_did=ah_did,
            comment=suggestion_meta.comment,
            media_upload_cb=media_sync.start_media_upload,
            auto_accept=suggestion_meta.auto_accept,
        )
        show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)


def open_suggestion_dialog_for_bulk_suggestion(
    anki_nids: Collection[NoteId], parent: QWidget
) -> None:
    """Opens a dialog for creating a bulk suggestion for the given notes.
    The notes have to be present in the Anki collection before calling this
    function and they need to have an AnkiHub note type.
    This function may change the notes contents (e.g. by renaming media files)
    and therefore the notes might need to be reloaded after this function is
    called."""

    ah_did = _determine_ah_did_for_nids_to_be_suggested(
        anki_nids=anki_nids, parent=parent
    )
    if ah_did is None:
        LOGGER.info("Bulk suggestion cancelled.")
        return

    notes = [aqt.mw.col.get_note(nid) for nid in anki_nids]

    suggestion_meta = SuggestionDialog(
        is_new_note_suggestion=False,
        is_for_anking_deck=ah_did == ANKING_DECK_ID,
        # We currently have a limit of 500 notes per bulk suggestion, so we don't have to worry
        # about performance here.
        added_new_media=any(_added_new_media(note) for note in notes),
    ).run()
    if not suggestion_meta:
        LOGGER.info("User cancelled bulk suggestion from suggestion dialog.")
        return

    aqt.mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            ankihub_did=ah_did,
            notes=notes,
            auto_accept=suggestion_meta.auto_accept,
            change_type=suggestion_meta.change_type,
            comment=_comment_with_source(suggestion_meta),
            media_upload_cb=media_sync.start_media_upload,
        ),
        on_done=lambda future: _on_suggest_notes_in_bulk_done(future, parent),
        parent=parent,
    )


def _determine_ah_did_for_nids_to_be_suggested(
    anki_nids: Collection[NoteId], parent: QWidget
) -> Optional[uuid.UUID]:
    """Return an AnkiHub deck id that the notes will be suggested to. If the
    choice of deck is ambiguous, the user is asked to choose a deck from a list
    of viable decks.
    Returns None if the user cancelled the deck selection dialog or if there is
    no deck that all notes could belong to."""
    anki_nid_to_possible_ah_dids = get_anki_nid_to_possible_ah_dids_dict(anki_nids)
    dids_that_all_notes_could_belong_to = set.intersection(
        *anki_nid_to_possible_ah_dids.values()
    )
    if len(dids_that_all_notes_could_belong_to) == 0:
        LOGGER.info(
            "User tried to submit suggestions for notes that could not belong to a single AnkiHub deck."
        )
        show_info("Please choose notes for one AnkiHub deck only.", parent=parent)
        return None
    elif len(dids_that_all_notes_could_belong_to) == 1:
        ah_did = dids_that_all_notes_could_belong_to.pop()
    else:
        ah_did = choose_ankihub_deck(
            prompt=(
                "Which AnkiHub deck would you like to submit your suggestion(s) to?<br><br>"
                "<i>A note type is used in multiple decks so AnkiHub can't determine<br>"
                "the deck automatically.</i>"
            ),
            ah_dids=list(dids_that_all_notes_could_belong_to),
            parent=parent,
        )
        if not ah_did:
            LOGGER.info("User cancelled bulk suggestion.")
            return None

    return ah_did


def _added_new_media(note: Note) -> bool:
    """Returns True if media files were added to the notes when comparing with
    the notes in the ankihub database, else False."""
    note_info_anki = to_note_data(note)
    media_names_anki = get_media_names_from_note_info(note_info_anki)

    note_info_ah = ankihub_db.note_data(note.id)
    if note_info_ah is None:
        return bool(media_names_anki)

    media_names_ah = get_media_names_from_note_info(note_info_ah)

    added_media_names = set(media_names_anki) - set(media_names_ah)
    result = len(added_media_names) > 0
    return result


def _comment_with_source(suggestion_meta: SuggestionMetadata) -> str:
    result = suggestion_meta.comment
    if suggestion_meta.source:
        result += f"\nSource: {suggestion_meta.source.source_type.value} - {suggestion_meta.source.source_text}"

    return result


def _on_suggest_notes_in_bulk_done(future: Future, parent: QWidget) -> None:
    try:
        suggestions_result: BulkNoteSuggestionsResult = future.result()
    except AnkiHubHTTPError as e:
        if e.response.status_code == 403:
            response_data = e.response.json()
            error_message = response_data.get("detail")
            if error_message:
                show_error_dialog(
                    error_message,
                    parent=parent,
                    title="Error submitting bulk suggestion :(",
                )
            else:
                raise e
        else:
            raise e

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

    # Emitted when the validation result was determined after self._validate was called.
    # The _validate method is called when the user changes the input in form elements that get validated.
    validation_signal = pyqtSignal(bool)

    def __init__(
        self,
        is_new_note_suggestion: bool,
        is_for_anking_deck: bool,
        added_new_media: bool,
    ) -> None:
        super().__init__()
        self._is_new_note_suggestion = is_new_note_suggestion
        self._is_for_anking_deck = is_for_anking_deck
        self._added_new_media = added_new_media

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

        # Add note about media source if a media file was added
        if self._added_new_media and self._is_for_anking_deck:
            label = QLabel(
                "Please provide the source of images or audio files<br>"
                "in the rationale field. For example:<br>"
                "Photo credit: The AnKing [www.ankingmed.com]"
            )
            self.layout_.addWidget(label)
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

        return self.suggestion_meta()

    def suggestion_meta(self) -> Optional[SuggestionMetadata]:
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
            and self._is_for_anking_deck
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

        if self._source_needed() and not self.source_widget.is_valid():
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

    # Emitted when the validation result was determined after self._validate was called.
    # The _validate method is called when the user changes the input in form elements that get validated.
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

        return SuggestionSource(source_type=source_type, source_text=source)

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
