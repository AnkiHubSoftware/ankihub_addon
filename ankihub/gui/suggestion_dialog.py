"""Dialog for creating a suggestion for a note or a bulk suggestion for multiple notes."""

import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import Callable, Collection, Dict, List, Optional, Sequence, Set

import aqt
from anki.models import NotetypeId
from anki.notes import Note, NoteId
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.utils import show_info, showInfo, showText

from .. import LOGGER
from ..ankihub_client import (
    AnkiHubHTTPError,
    NoteInfo,
    SuggestionType,
    get_media_names_from_note_info,
)
from ..ankihub_client.models import UserDeckRelation
from ..db import ankihub_db
from ..main.exporting import to_note_data
from ..main.suggestions import (
    ANKIHUB_EMPTY_FIRST_FIELD_ERROR,
    ANKIHUB_NO_CHANGE_ERROR,
    ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR,
    BulkNoteSuggestionsResult,
    ChangeSuggestionResult,
    edited_field_names,
    get_anki_nid_to_ah_dids_dict,
    is_new_suggest_workflow_enabled,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
    tag_changes,
)
from ..settings import RATIONALE_FOR_CHANGE_MAX_LENGTH, config
from .errors import report_exception_and_upload_logs
from .media_sync import media_sync
from .utils import (
    active_window_or_mw,
    show_error_dialog,
    show_tooltip,
)


class SourceType(Enum):
    AMBOSS = "AMBOSS"
    UWORLD = "UWorld"
    SOCIETY_GUIDELINES = "Society Guidelines"
    DUPLICATE_NOTE = "Duplicate Note"
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
    # User-selected filters for the outgoing suggestion. None = unfiltered (today's behavior).
    fields_to_include_by_mid: Optional[Dict[NotetypeId, List[str]]] = None
    tags_to_add: Optional[List[str]] = None
    tags_to_remove: Optional[List[str]] = None


def open_suggestion_dialog_for_single_suggestion(
    note: Note,
    parent: QWidget,
    preselected_change_type: Optional[SuggestionType] = None,
) -> None:
    """Opens a dialog for creating a note suggestion for the given note.
    The note has to be present in the Anki collection before calling this function.

    The preselected_change_type will be preselected in the
    change type dropdown when the dialog is opened.
    """

    assert ankihub_db.is_ankihub_note_type(note.mid), f"Note type {note.mid} is not associated with an AnkiHub deck."

    ah_did = _determine_ah_did_for_nids_to_be_suggested([note.id], parent)
    if not ah_did:
        LOGGER.info("Suggestion cancelled.", note_id=note.id)
        return

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    SuggestionDialog(
        is_new_note_suggestion=ah_nid is None,
        is_for_anking_deck=ah_did == config.anking_deck_id,
        can_submit_without_review=_can_submit_without_review(ah_did=ah_did),
        added_new_media=_added_new_media(note),
        callback=lambda suggestion_meta: _on_suggestion_dialog_for_single_suggestion_closed(
            suggestion_meta=suggestion_meta,
            note=note,
            ah_did=ah_did,
            parent=parent,
        ),
        notes=[note],
        ah_did=ah_did,
        preselected_change_type=preselected_change_type,
        parent=parent,
    )


def _handle_suggestion_error(e: AnkiHubHTTPError, parent: QWidget) -> None:
    if "suggestion" not in e.response.url:
        raise e

    if e.response.status_code == 400:
        if non_field_errors := e.response.json().get("non_field_errors", None):
            error_message = "\n".join(non_field_errors)
        else:
            error_message = pformat(e.response.json())
            # these errors are not expected and should be reported
            report_exception_and_upload_logs(e)
        all_no_changes_errors = all(ANKIHUB_NO_CHANGE_ERROR in error for error in non_field_errors)
        if all_no_changes_errors:
            # The dialog OK button is gated on at least one field/tag change being
            # selected, so this branch should be unreachable in normal use. If it
            # still fires (e.g. sync racing with submit), fall through to a tooltip.
            show_tooltip("No changes to suggest.", parent=parent)
        else:
            showInfo(
                text=(f"There are some problems with this suggestion:<br><br><b>{error_message}</b>"),
                title="Problem with suggestion",
            )
        LOGGER.info("Can't submit suggestion.", error_message=error_message)
    elif e.response.status_code == 403:
        response_data = e.response.json()
        error_message = response_data.get("detail")
        if error_message:
            show_error_dialog(
                error_message,
                parent=parent,
                title="Error submitting suggestion :(",
            )
        else:
            raise e
    else:
        raise e


def _on_suggestion_dialog_for_single_suggestion_closed(
    suggestion_meta: SuggestionMetadata,
    note: Note,
    ah_did: uuid.UUID,
    parent: QWidget,
) -> None:
    if suggestion_meta is None:
        return

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    if ah_nid:
        fields_for_note: Optional[List[str]] = None
        if suggestion_meta.fields_to_include_by_mid is not None:
            fields_for_note = suggestion_meta.fields_to_include_by_mid.get(NotetypeId(note.mid), [])
        try:
            suggestion_result = suggest_note_update(
                note=note,
                change_type=suggestion_meta.change_type,
                comment=_comment_with_source(suggestion_meta),
                media_upload_cb=media_sync.start_media_upload,
                auto_accept=suggestion_meta.auto_accept,
                fields_to_include=fields_for_note,
                tags_to_add=suggestion_meta.tags_to_add,
                tags_to_remove=suggestion_meta.tags_to_remove,
            )
        except AnkiHubHTTPError as e:
            _handle_suggestion_error(e, parent)
            return
        if suggestion_result == ChangeSuggestionResult.SUCCESS:
            show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        elif suggestion_result == ChangeSuggestionResult.NO_CHANGES:
            show_tooltip("No changes. Try syncing with AnkiHub first.", parent=parent)
        elif suggestion_result == ChangeSuggestionResult.EMPTY_FIRST_FIELD:
            show_tooltip("Suggestion was not created because the first field is required.", parent=parent)
        elif suggestion_result == ChangeSuggestionResult.ANKIHUB_NOT_FOUND:
            show_error_dialog(
                "This note has been deleted from AnkiHub. No new suggestions can be made.",
                title="Note has been deleted from AnkiHub.",
                parent=parent,
            )
        else:
            raise ValueError(  # pragma: no cover
                f"Unknown suggestion result: {suggestion_result}"
            )
    else:
        # Check for empty first field before submitting new note suggestion
        if not note.fields or not note.fields[0].strip():
            show_tooltip("The first field is required.", parent=parent)
            return

        try:
            suggest_new_note(
                note=note,
                ankihub_did=ah_did,
                comment=suggestion_meta.comment,
                media_upload_cb=media_sync.start_media_upload,
                auto_accept=suggestion_meta.auto_accept,
            )
            show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        except AnkiHubHTTPError as e:
            _handle_suggestion_error(e, parent)


def open_suggestion_dialog_for_bulk_suggestion(
    anki_nids: Collection[NoteId],
    parent: QWidget,
    preselected_change_type: Optional[SuggestionType] = None,
) -> None:
    """Opens a dialog for creating a bulk suggestion for the given notes.
    The notes have to be present in the Anki collection before calling this
    function and they need to have an AnkiHub note type.

    The preselected_change_type will be preselected in the
    change type dropdown when the dialog is opened.
    """

    ah_did = _determine_ah_did_for_nids_to_be_suggested(anki_nids=anki_nids, parent=parent)
    if ah_did is None:
        LOGGER.info("Bulk suggestion cancelled.")
        return

    notes = [aqt.mw.col.get_note(nid) for nid in anki_nids]

    SuggestionDialog(
        is_new_note_suggestion=False,
        is_for_anking_deck=ah_did == config.anking_deck_id,
        can_submit_without_review=_can_submit_without_review(ah_did=ah_did),
        # We currently have a limit of 500 notes per bulk suggestion, so we don't have to worry
        # about performance here.
        added_new_media=any(_added_new_media(note) for note in notes),
        callback=lambda suggestion_meta: _on_suggestion_dialog_for_bulk_suggestion_closed(
            suggestion_meta=suggestion_meta,
            notes=notes,
            ah_did=ah_did,
            parent=parent,
        ),
        notes=notes,
        ah_did=ah_did,
        preselected_change_type=preselected_change_type,
        parent=parent,
    )


def _on_suggestion_dialog_for_bulk_suggestion_closed(
    suggestion_meta: SuggestionMetadata,
    notes: List[Note],
    ah_did: uuid.UUID,
    parent: QWidget,
) -> None:
    if suggestion_meta is None:
        LOGGER.info("User cancelled bulk suggestion from suggestion dialog.")
        return

    def media_upload_cb(media_names: Set[str], ankihub_did: uuid.UUID) -> None:
        aqt.mw.taskman.run_on_main(
            lambda: media_sync.start_media_upload(media_names=media_names, ankihub_did=ankihub_did)
        )

    aqt.mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            ankihub_did=ah_did,
            notes=notes,
            auto_accept=suggestion_meta.auto_accept,
            change_type=suggestion_meta.change_type,
            comment=_comment_with_source(suggestion_meta),
            media_upload_cb=media_upload_cb,
            fields_to_include_by_mid=suggestion_meta.fields_to_include_by_mid,
            tags_to_add=suggestion_meta.tags_to_add,
            tags_to_remove=suggestion_meta.tags_to_remove,
        ),
        on_done=lambda future: _on_suggest_notes_in_bulk_done(future, parent),
        parent=parent,
    )


def _can_submit_without_review(ah_did: uuid.UUID) -> bool:
    result = config.deck_config(ah_did).user_relation in [
        UserDeckRelation.OWNER,
        UserDeckRelation.MAINTAINER,
    ]
    return result


def _determine_ah_did_for_nids_to_be_suggested(anki_nids: Collection[NoteId], parent: QWidget) -> Optional[uuid.UUID]:
    """Return an AnkiHub deck id that the notes will be suggested to. If the
    choice of deck is ambiguous, the user is asked to choose a deck from a list
    of viable decks.
    Returns None if the user cancelled the deck selection dialog or if notes don't belong to AnkiHub deck.
    """
    anki_nid_to_ah_did = get_anki_nid_to_ah_dids_dict(anki_nids)
    ah_dids = set(anki_nid_to_ah_did.values())

    if len(ah_dids) == 0:
        LOGGER.info("User tried to submit suggestions for notes which don't belong to any AnkiHub deck.")
        return None
    if len(ah_dids) != 1:
        LOGGER.info("User tried to submit suggestions for notes that belong to multiple AnkiHub decks.")
        show_info("Please choose notes for one AnkiHub deck only.", parent=parent)
        return None

    ah_did = list(ah_dids)[0]
    return ah_did


def _added_new_media(note: Note) -> bool:
    """Returns True if media files were added to the notes when comparing with
    the notes in the ankihub database, else False."""
    note_info_anki = to_note_data(note, include_protected_fields=is_new_suggest_workflow_enabled())
    media_names_anki = get_media_names_from_note_info(note_info_anki, note.note_type())

    note_info_ah = ankihub_db.note_data(note.id)
    if note_info_ah is None:
        return bool(media_names_anki)

    media_names_ah = get_media_names_from_note_info(note_info_ah, note.note_type())

    added_media_names = set(media_names_anki) - set(media_names_ah)
    result = len(added_media_names) > 0
    return result


def _comment_with_source(suggestion_meta: SuggestionMetadata) -> str:
    result = suggestion_meta.comment
    if suggestion_meta.source and suggestion_meta.source.source_text.strip():
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
                    message=error_message,
                    parent=parent,
                    title="Error submitting bulk suggestion :(",
                )
                return
        raise e

    LOGGER.info(
        "Created note suggestions in bulk.",
        errors_by_nid=suggestions_result.errors_by_nid,
    )

    msg_about_created_suggestions = (
        f"Submitted {suggestions_result.change_note_suggestions_count} change note suggestion(s).\n"
        f"Submitted {suggestions_result.new_note_suggestions_count} new note suggestion(s).\n\n"
    )

    notes_without_changes = [
        note for note, errors in suggestions_result.errors_by_nid.items() if ANKIHUB_NO_CHANGE_ERROR in str(errors)
    ]
    notes_that_dont_exist_on_ankihub = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR in str(errors)
    ]
    notes_with_empty_first_field = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if ANKIHUB_EMPTY_FIRST_FIELD_ERROR in str(errors)
    ]

    if suggestions_result.errors_by_nid:
        category_messages = []
        if notes_without_changes:
            category_messages.append(
                f"Notes without changes ({len(notes_without_changes)}):\n"
                f"{', '.join(str(nid) for nid in notes_without_changes)}"
            )
        if notes_that_dont_exist_on_ankihub:
            category_messages.append(
                f"Notes that don't exist on AnkiHub ({len(notes_that_dont_exist_on_ankihub)}):\n"
                f"{', '.join(str(nid) for nid in notes_that_dont_exist_on_ankihub)}"
            )
        if notes_with_empty_first_field:
            category_messages.append(
                f"Notes with the first field empty ({len(notes_with_empty_first_field)}):\n"
                f"{', '.join(str(nid) for nid in notes_with_empty_first_field)}"
            )

        msg_about_failed_suggestions = (
            f"Failed to submit suggestions for {len(suggestions_result.errors_by_nid)} note(s).\n"
            "All notes with failed suggestions:\n"
            f"{', '.join(str(nid) for nid in suggestions_result.errors_by_nid.keys())}\n\n"
            + "\n\n".join(category_messages)
        )
    else:
        msg_about_failed_suggestions = ""

    msg = msg_about_created_suggestions + msg_about_failed_suggestions
    showText(txt=msg, parent=parent, title="AnkiHub | Bulk Suggestion Summary")


class SuggestionDialog(QDialog):
    silentlyClose = True

    # Emitted when the validation result was determined after self._validate was called.
    # The _validate method is called when the user changes the input in form elements that get validated.
    validation_signal = pyqtSignal(bool)

    def __init__(
        self,
        is_new_note_suggestion: bool,
        is_for_anking_deck: bool,
        can_submit_without_review: bool,
        added_new_media: bool,
        callback: Callable[[Optional[SuggestionMetadata]], None],
        notes: Sequence[Note] = (),
        ah_did: Optional[uuid.UUID] = None,
        preselected_change_type: Optional[SuggestionType] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        if parent is None:
            parent = active_window_or_mw()

        super().__init__(parent)
        self._is_new_note_suggestion = is_new_note_suggestion
        self._is_for_anking_deck = is_for_anking_deck
        self._can_submit_without_review = can_submit_without_review
        self._added_new_media = added_new_media
        self._callback = callback
        self._notes = list(notes)
        self._ah_did = ah_did
        self._preselected_change_type = preselected_change_type
        self._fields_widget: Optional[FieldsToSuggestWidget] = None

        self._setup_ui()

        if preselected_change_type:
            self.change_type_select.setCurrentText(preselected_change_type.value[1])

        self.show()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Note Suggestion(s)")

        # Two-column layout: Fields-to-Suggest panel on the left, everything else on the right.
        # Left column is hidden when the panel isn't applicable (new-note, DELETE, flag off).
        main_layout = QHBoxLayout()
        self.setLayout(main_layout)

        if (
            is_new_suggest_workflow_enabled()
            and not self._is_new_note_suggestion
            and self._notes
            and self._ah_did is not None
        ):
            self._fields_widget = FieldsToSuggestWidget(notes=self._notes, ah_did=self._ah_did)
            self._fields_widget.setMinimumWidth(220)
            main_layout.addWidget(self._fields_widget)
            qconnect(self._fields_widget.selection_changed, self._validate)

        # Right column holds the form
        right_layout = QVBoxLayout()
        main_layout.addLayout(right_layout, 1)

        # Set up change type dropdown
        self.change_type_select = QComboBox()
        if not self._is_new_note_suggestion:
            self.change_type_select.addItems([x.value[1] for x in SuggestionType])
            right_layout.addWidget(QLabel("Change Type"))
            right_layout.addWidget(self.change_type_select)
            qconnect(
                self.change_type_select.currentTextChanged,
                self._on_change_type_changed,
            )
            right_layout.addSpacing(10)

        # Set up source widget in a group box (group box is for styling purposes)
        self.source_widget = SourceWidget()
        self.source_widget_group_box = QGroupBox("Source")
        right_layout.addWidget(self.source_widget_group_box)
        self.source_widget_group_box_layout = QVBoxLayout()
        self.source_widget_group_box.setLayout(self.source_widget_group_box_layout)

        self.source_widget_group_box_layout.addWidget(self.source_widget)
        qconnect(self.source_widget.validation_signal, self._validate)
        right_layout.addSpacing(10)

        self._refresh_source_widget()

        self.hint_for_note_deletions = QLabel("💡 When deleting a note, any changes<br>to fields will not be applied.")
        self.hint_for_note_deletions.hide()
        right_layout.addWidget(self.hint_for_note_deletions)
        right_layout.addSpacing(10)

        # Set up rationale field
        right_layout.addWidget(QLabel("Rationale for Change (Required)"))

        self.rationale_edit = QPlainTextEdit()
        self.rationale_edit.setPlaceholderText(RATIONALE_FOR_CHANGE_PLACEHOLDER)
        right_layout.addWidget(self.rationale_edit)

        def limit_length():
            while len(self.rationale_edit.toPlainText()) >= RATIONALE_FOR_CHANGE_MAX_LENGTH:
                self.rationale_edit.textCursor().deletePreviousChar()

        qconnect(self.rationale_edit.textChanged, limit_length)
        qconnect(self.rationale_edit.textChanged, self._validate)

        right_layout.addSpacing(10)

        # Add note about media source if a media file was added
        if self._added_new_media and self._is_for_anking_deck:
            right_layout.addWidget(
                QLabel(
                    "Please provide the source of images or audio files<br>"
                    "in the rationale field. For example:<br>"
                    "Photo credit: The AnKing [www.ankingmed.com]"
                )
            )
            right_layout.addSpacing(10)

        # Set up "auto-accept" checkbox
        self.auto_accept_cb = QCheckBox("Submit without review.")
        self.auto_accept_cb.setVisible(self._can_submit_without_review)
        right_layout.addWidget(self.auto_accept_cb)

        # Set up button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)
        right_layout.addWidget(self.button_box)

        # Now that change_type_select exists, refresh widget visibility (depends on _change_type()).
        if self._fields_widget is not None:
            self._refresh_fields_widget_visibility()

        self._set_submit_button_enabled_state(False)
        qconnect(self.validation_signal, self._set_submit_button_enabled_state)

    def accept(self) -> None:
        if self._fields_widget_active():
            self._fields_widget.save_selection()
        self._callback(self.suggestion_meta())
        super().accept()

    def reject(self) -> None:
        self._callback(None)
        super().reject()

    def _fields_widget_active(self) -> bool:
        """The Fields-to-Suggest selector is in scope (not new-note) and visible (not DELETE)."""
        return self._fields_widget is not None and self._fields_widget.isVisible()

    def suggestion_meta(self) -> Optional[SuggestionMetadata]:
        fields_filter: Optional[Dict[NotetypeId, List[str]]] = None
        tags_to_add: Optional[List[str]] = None
        tags_to_remove: Optional[List[str]] = None
        if self._fields_widget_active():
            fields_filter = self._fields_widget.selected_field_names_by_mid()
            tags_to_add = self._fields_widget.selected_tag_additions()
            tags_to_remove = self._fields_widget.selected_tag_removals()
        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
            source=(self.source_widget.suggestion_source() if self._source_needed() else None),
            fields_to_include_by_mid=fields_filter,
            tags_to_add=tags_to_add,
            tags_to_remove=tags_to_remove,
        )

    def _on_change_type_changed(self) -> None:
        self._refresh_source_widget()
        self._refresh_hint_for_note_deletions()
        self._refresh_fields_widget_visibility()
        self._validate()

    def _refresh_fields_widget_visibility(self) -> None:
        if self._fields_widget is None:
            return
        # Hide for DELETE — the field-selection logic doesn't apply.
        self._fields_widget.setVisible(self._change_type() != SuggestionType.DELETE)

    def _refresh_source_widget(self):
        if self._source_needed():
            self.source_widget.setup_for_change_type(change_type=self._change_type())
            self.source_widget_group_box.show()
        else:
            self.source_widget_group_box.hide()

    def _source_needed(self) -> bool:
        return (
            self._change_type()
            in [
                SuggestionType.NEW_CONTENT,
                SuggestionType.UPDATED_CONTENT,
            ]
            and self._is_for_anking_deck
        ) or (self._change_type() == SuggestionType.DELETE)

    def _refresh_hint_for_note_deletions(self) -> None:
        self.hint_for_note_deletions.setVisible(self._change_type() == SuggestionType.DELETE)

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

        if self._fields_widget_active() and not self._fields_widget.has_any_selection():
            # Covers both "user unchecked everything" and "no edits to suggest in the first place"
            # — submitting either would just produce a NO_CHANGES tooltip on the server round-trip.
            return False

        return True

    def _change_type(self) -> Optional[SuggestionType]:
        if self._is_new_note_suggestion:
            return None
        else:
            return next(x for x in SuggestionType if x.value[1] == self.change_type_select.currentText())

    def _comment(self) -> str:
        return self.rationale_edit.toPlainText()

    def _auto_accept(self) -> bool:
        return self.auto_accept_cb.isChecked()


# Maps change types to source types that are available for that change type.
change_type_to_source_types = {
    SuggestionType.NEW_CONTENT: [
        SourceType.AMBOSS,
        SourceType.UWORLD,
        SourceType.SOCIETY_GUIDELINES,
        SourceType.OTHER,
    ],
    SuggestionType.UPDATED_CONTENT: [
        SourceType.AMBOSS,
        SourceType.UWORLD,
        SourceType.SOCIETY_GUIDELINES,
        SourceType.OTHER,
    ],
    SuggestionType.DELETE: [
        SourceType.DUPLICATE_NOTE,
    ],
}

# Maps source types to the label that is shown next to the source input field.
source_type_to_source_label = {
    SourceType.AMBOSS: "Link",
    SourceType.UWORLD: "UWorld Question ID",
    SourceType.SOCIETY_GUIDELINES: "Link",
    SourceType.DUPLICATE_NOTE: "Anki note ID of duplicate note",
    SourceType.OTHER: "More details",
}

# Maps source types to the placeholder text that is shown in the source input field.
# If a source type is not in this dict, no placeholder text is shown.
source_type_to_source_place_holder_text = {
    SourceType.AMBOSS: "Paste AMBOSS link",
    SourceType.UWORLD: "e.g. 12345",
    SourceType.SOCIETY_GUIDELINES: "Paste link to guidelines",
    SourceType.DUPLICATE_NOTE: "[Include ID, if applicable]",
    SourceType.OTHER: "Describe the source",
}

# Placeholder shown in the Rationale-for-change textarea.
RATIONALE_FOR_CHANGE_PLACEHOLDER = "Why should this change be applied?"

source_types_where_input_is_optional = [
    SourceType.DUPLICATE_NOTE,
]

# Options for the UWorld step select dropdown.
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
        self.layout_.addWidget(self.source_type_select)
        qconnect(self.source_type_select.currentTextChanged, self._on_source_type_change)

        # Setup space below source type select.
        # Its size will be changed depending on whether the source type select is visible or not.
        self.space_below_source_type_select = QSpacerItem(0, 10)
        self.layout_.addSpacerItem(self.space_below_source_type_select)

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
        qconnect(self.source_edit.textChanged, self._validate)
        self.layout_.addWidget(self.source_edit)

    def setup_for_change_type(self, change_type: SuggestionType) -> None:
        """Sets up the source widget for the given change type. This method should be called initially
        after the source widget was created and whenever the change type changes."""
        source_types = change_type_to_source_types[change_type]

        self.source_type_select.clear()
        self.source_type_select.addItems([source_type.value for source_type in source_types])

        if len(source_types) > 1:
            self.source_type_select.show()
            self.space_below_source_type_select.changeSize(0, 10)
        else:
            # The source type select is not necessary if there is only one source type option.
            self.source_type_select.hide()
            self.space_below_source_type_select.changeSize(0, 0)

        self._refresh_source_input_label()

    def suggestion_source(self) -> SuggestionSource:
        source_type = self._source_type()
        source = self.source_edit.text()

        if source_type == SourceType.UWORLD:
            step = self.uworld_step_select.currentText()
            source = f"{step} {source}"

        return SuggestionSource(source_type=source_type, source_text=source)

    def is_valid(self) -> bool:
        if self._source_type() in source_types_where_input_is_optional:
            return True

        text = self.source_edit.text().strip()
        return len(text) > 0

    def _validate(self) -> None:
        if not self.is_valid():
            self.validation_signal.emit(False)
        else:
            self.validation_signal.emit(True)

    def _on_source_type_change(self) -> None:
        source_type = self._source_type()
        if source_type is None:
            return

        self._refresh_source_input_label()
        if source_type == SourceType.UWORLD:
            self.uworld_step_select.show()
            self.space_after_uworld_step_select.changeSize(0, 10)
            self.layout_.invalidate()
        else:
            self.uworld_step_select.hide()
            self.space_after_uworld_step_select.changeSize(0, 0)
            self.layout_.invalidate()

    def _refresh_source_input_label(self) -> None:
        text = source_type_to_source_label[self._source_type()]

        self.source_input_label.setText(text)

        place_holder_text = source_type_to_source_place_holder_text.get(self._source_type(), "")
        self.source_edit.setPlaceholderText(place_holder_text)

    def _source_type(self) -> Optional[SourceType]:
        if self.source_type_select.currentText():
            return SourceType(self.source_type_select.currentText())
        else:
            return None


class FieldsToSuggestWidget(QWidget):
    """Selector for which edited fields and tag changes to include in the outgoing
    suggestion. Visible only for change-suggestion flow (skipped for new-note and
    delete flows).

    Single-note: flat list. Bulk: one group per note type. Globally-protected fields
    appear inline indistinguishably; the server/reviewers decide whether to apply them.
    """

    selection_changed = pyqtSignal()

    def __init__(self, notes: Sequence[Note], ah_did: uuid.UUID, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._notes = list(notes)
        self._ah_did = ah_did
        self._field_checkboxes: Dict[NotetypeId, Dict[str, QCheckBox]] = {}
        self._added_tag_boxes: Dict[str, QCheckBox] = {}
        self._removed_tag_boxes: Dict[str, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)

        title_row = QHBoxLayout()
        self._title_label = QLabel("<b>Fields to Suggest</b>")
        self._counter_label = QLabel("")
        title_row.addWidget(self._title_label)
        title_row.addStretch()
        title_row.addWidget(self._counter_label)
        outer.addLayout(title_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(120)
        body = QWidget()
        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(4, 4, 4, 4)
        body.setLayout(self._body_layout)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll)

        self._populate()
        self._refresh_counter()

    def _populate(self) -> None:
        is_bulk = len(self._notes) > 1

        # Batch-fetch the AnkiHub-stored versions to avoid an N+1 DB pattern in bulk mode.
        ah_notes_by_anki_nid: Dict[NoteId, NoteInfo] = {
            NoteId(ah_note.anki_nid): ah_note
            for ah_note in ankihub_db.notes_data_for_anki_nids([note.id for note in self._notes])
        }

        # Union of edited fields per note type across all notes of that type (bulk).
        fields_by_mid: Dict[NotetypeId, List[str]] = {}
        note_type_name_by_mid: Dict[NotetypeId, str] = {}
        for note in self._notes:
            mid = NotetypeId(note.mid)
            note_type_name_by_mid.setdefault(mid, note.note_type()["name"])
            fields = edited_field_names(note, ah_note=ah_notes_by_anki_nid.get(note.id))
            fields_by_mid[mid] = list(dict.fromkeys((*fields_by_mid.get(mid, ()), *fields)))

        for mid, fields in fields_by_mid.items():
            if not fields:
                continue
            deselected = set(config.last_deselected_fields(self._ah_did, mid))

            if is_bulk:
                group = QGroupBox(f"Note type: {note_type_name_by_mid[mid]}")
                group_layout = QVBoxLayout()
                group.setLayout(group_layout)
                container = group_layout
                self._body_layout.addWidget(group)
            else:
                container = self._body_layout

            mid_map: Dict[str, QCheckBox] = {}
            for field_name in fields:
                cb = QCheckBox(field_name)
                cb.setChecked(field_name not in deselected)
                qconnect(cb.toggled, self._on_toggle)
                container.addWidget(cb)
                mid_map[field_name] = cb
            self._field_checkboxes[mid] = mid_map

        # Tag changes are listed in a single dialog-level section as the union across all notes.
        added_tags: List[str] = []
        removed_tags: List[str] = []
        for note in self._notes:
            added, removed = tag_changes(note, ah_note=ah_notes_by_anki_nid.get(note.id))
            added_tags.extend(added)
            removed_tags.extend(removed)
        added_tags = list(dict.fromkeys(added_tags))
        removed_tags = list(dict.fromkeys(removed_tags))

        if added_tags or removed_tags:
            tag_group = QGroupBox("Tag changes")
            tag_layout = QVBoxLayout()
            tag_group.setLayout(tag_layout)
            for tag in added_tags:
                cb = QCheckBox(f"+ {tag}")
                cb.setChecked(True)
                qconnect(cb.toggled, self._on_toggle)
                tag_layout.addWidget(cb)
                self._added_tag_boxes[tag] = cb
            for tag in removed_tags:
                cb = QCheckBox(f"− {tag}")
                cb.setChecked(True)
                qconnect(cb.toggled, self._on_toggle)
                tag_layout.addWidget(cb)
                self._removed_tag_boxes[tag] = cb
            self._body_layout.addWidget(tag_group)

        self._body_layout.addStretch()

    def _on_toggle(self, _checked: bool) -> None:
        self._refresh_counter()
        self.selection_changed.emit()

    def _all_checkboxes(self) -> List[QCheckBox]:
        boxes: List[QCheckBox] = []
        for mid_map in self._field_checkboxes.values():
            boxes.extend(mid_map.values())
        boxes.extend(self._added_tag_boxes.values())
        boxes.extend(self._removed_tag_boxes.values())
        return boxes

    def _refresh_counter(self) -> None:
        boxes = self._all_checkboxes()
        total = len(boxes)
        selected = sum(1 for cb in boxes if cb.isChecked())
        self._counter_label.setText(f"{selected} / {total} selected" if total else "")

    def has_any_edits(self) -> bool:
        """True if there's anything to potentially suggest (regardless of selection)."""
        return bool(self._all_checkboxes())

    def has_any_selection(self) -> bool:
        return any(cb.isChecked() for cb in self._all_checkboxes())

    def selected_field_names_by_mid(self) -> Dict[NotetypeId, List[str]]:
        return {
            mid: [name for name, cb in mid_map.items() if cb.isChecked()]
            for mid, mid_map in self._field_checkboxes.items()
        }

    def selected_tag_additions(self) -> List[str]:
        return [tag for tag, cb in self._added_tag_boxes.items() if cb.isChecked()]

    def selected_tag_removals(self) -> List[str]:
        return [tag for tag, cb in self._removed_tag_boxes.items() if cb.isChecked()]

    def save_selection(self) -> None:
        """Persist the user's deselected-fields choice per (ah_did, mid). Called on dialog accept.

        Merges with prior state so a deselection survives sessions where the same field isn't
        edited (and therefore isn't shown in the widget). Explicit re-selection in the current
        session clears the deselection.
        """
        for mid, mid_map in self._field_checkboxes.items():
            currently_selected = {name for name, cb in mid_map.items() if cb.isChecked()}
            currently_deselected = {name for name, cb in mid_map.items() if not cb.isChecked()}
            prior = set(config.last_deselected_fields(self._ah_did, mid))
            merged = (prior - currently_selected) | currently_deselected
            config.set_last_deselected_fields(self._ah_did, mid, sorted(merged))
