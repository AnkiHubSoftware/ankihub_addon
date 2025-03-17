"""Dialog for creating a suggestion for a note or a bulk suggestion for multiple notes."""

import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from pprint import pformat
from typing import Callable, Collection, List, Optional, Set

import aqt
from anki.notes import Note, NoteId
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QCursor,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpacerItem,
    Qt,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.utils import show_info, showInfo, showText

from .. import LOGGER
from ..ankihub_client import (
    AnkiHubHTTPError,
    SuggestionType,
    get_media_names_from_note_info,
)
from ..ankihub_client.models import UserDeckRelation
from ..db import ankihub_db
from ..main.exporting import to_note_data
from ..main.suggestions import (
    ANKIHUB_NO_CHANGE_ERROR,
    BulkNoteSuggestionsResult,
    ChangeSuggestionResult,
    get_anki_nid_to_ah_dids_dict,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)
from ..settings import RATIONALE_FOR_CHANGE_MAX_LENGTH, config
from .errors import report_exception_and_upload_logs
from .media_sync import media_sync
from .utils import (
    active_window_or_mw,
    show_dialog,
    show_error_dialog,
    show_tooltip,
    tooltip_icon,
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

    assert ankihub_db.is_ankihub_note_type(
        note.mid
    ), f"Note type {note.mid} is not associated with an AnkiHub deck."

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
        all_no_changes_errors = all(
            ANKIHUB_NO_CHANGE_ERROR in error for error in non_field_errors
        )
        if all_no_changes_errors:
            dialog = show_dialog(
                title="Invalid suggestion",
                text=(
                    "No field or tag changes were detected. "
                    "Please verify that the changes you made were not to a protected field and try again.<br><br>"
                ),
                parent=parent,
                open_dialog=False,
            )
            layout = dialog.content_layout
            sublayout = QHBoxLayout()
            subwidget = QWidget()
            subwidget.setLayout(sublayout)
            label = QLabel(
                "(Learn more about protected fields "
                "<a href='https://community.ankihub.net/t/protecting-fields-and-tags/165604'>here</a>.)"
            )
            sublayout.addWidget(label)
            icon_label = QLabel("")
            pixmap = tooltip_icon().pixmap(QCheckBox().iconSize())
            icon_label.setPixmap(pixmap)
            icon_label.setToolTip(
                "Protecting a field allows you to add anything you want to a field\n"
                "without it being overwritten via AnkiHub suggestions."
            )
            icon_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            sublayout.addWidget(icon_label)
            layout.insertWidget(layout.count() - 1, subwidget)
            dialog.adjustSize()
            dialog.open()
        else:
            showInfo(
                text=(
                    "There are some problems with this suggestion:<br><br>"
                    f"<b>{error_message}</b>"
                ),
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
        try:
            suggestion_result = suggest_note_update(
                note=note,
                change_type=suggestion_meta.change_type,
                comment=_comment_with_source(suggestion_meta),
                media_upload_cb=media_sync.start_media_upload,
                auto_accept=suggestion_meta.auto_accept,
            )
        except AnkiHubHTTPError as e:
            _handle_suggestion_error(e, parent)
            return
        if suggestion_result == ChangeSuggestionResult.SUCCESS:
            show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        elif suggestion_result == ChangeSuggestionResult.NO_CHANGES:
            show_tooltip("No changes. Try syncing with AnkiHub first.", parent=parent)
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

    ah_did = _determine_ah_did_for_nids_to_be_suggested(
        anki_nids=anki_nids, parent=parent
    )
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
            lambda: media_sync.start_media_upload(
                media_names=media_names, ankihub_did=ankihub_did
            )
        )

    aqt.mw.taskman.with_progress(
        task=lambda: suggest_notes_in_bulk(
            ankihub_did=ah_did,
            notes=notes,
            auto_accept=suggestion_meta.auto_accept,
            change_type=suggestion_meta.change_type,
            comment=_comment_with_source(suggestion_meta),
            media_upload_cb=media_upload_cb,
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


def _determine_ah_did_for_nids_to_be_suggested(
    anki_nids: Collection[NoteId], parent: QWidget
) -> Optional[uuid.UUID]:
    """Return an AnkiHub deck id that the notes will be suggested to. If the
    choice of deck is ambiguous, the user is asked to choose a deck from a list
    of viable decks.
    Returns None if the user cancelled the deck selection dialog or if notes don't belong to AnkiHub deck.
    """
    anki_nid_to_ah_did = get_anki_nid_to_ah_dids_dict(anki_nids)
    ah_dids = set(anki_nid_to_ah_did.values())

    if len(ah_dids) == 0:
        LOGGER.info(
            "User tried to submit suggestions for notes which don't belong to any AnkiHub deck."
        )
        return None
    if len(ah_dids) != 1:
        LOGGER.info(
            "User tried to submit suggestions for notes that belong to multiple AnkiHub decks."
        )
        show_info("Please choose notes for one AnkiHub deck only.", parent=parent)
        return None

    ah_did = list(ah_dids)[0]
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
        f"Submitted {suggestions_result.new_note_suggestions_count} new note suggestion(s).\n\n\n"
    )

    notes_without_changes = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if ANKIHUB_NO_CHANGE_ERROR in str(errors)
    ]
    notes_that_dont_exist_on_ankihub = [
        note
        for note, errors in suggestions_result.errors_by_nid.items()
        if "Note object does not exist" in str(errors)
    ]
    msg_about_failed_suggestions = (
        (
            f"Failed to submit suggestions for {len(suggestions_result.errors_by_nid)} note(s).\n"
            "All notes with failed suggestions:\n"
            f'{", ".join(str(nid) for nid in suggestions_result.errors_by_nid.keys())}\n\n'
            f"Notes without changes ({len(notes_without_changes)}):\n"
            f'{", ".join(str(nid) for nid in notes_without_changes)}\n\n'
            f"Notes that don't exist on AnkiHub ({len(notes_that_dont_exist_on_ankihub)}):\n"
            f'{", ".join(str(nid) for nid in notes_that_dont_exist_on_ankihub)}'
        )
        if suggestions_result.errors_by_nid
        else ""
    )

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
        self._preselected_change_type = preselected_change_type

        self._setup_ui()

        if preselected_change_type:
            self.change_type_select.setCurrentText(preselected_change_type.value[1])

        self.show()

    def _setup_ui(self) -> None:
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
                self._on_change_type_changed,
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
        self.layout_.addSpacing(10)

        self._refresh_source_widget()

        self.hint_for_note_deletions = QLabel(
            "💡 When deleting a note, any changes<br>to fields will not be applied."
        )
        self.hint_for_note_deletions.hide()
        self.layout_.addWidget(self.hint_for_note_deletions)
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
        self.auto_accept_cb = QCheckBox("Submit without review.")
        self.auto_accept_cb.setVisible(self._can_submit_without_review)
        self.layout_.addWidget(self.auto_accept_cb)

        # Set up button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)
        self.layout_.addWidget(self.button_box)

        self._set_submit_button_enabled_state(False)
        qconnect(self.validation_signal, self._set_submit_button_enabled_state)

    def accept(self) -> None:
        self._callback(self.suggestion_meta())
        super().accept()

    def reject(self) -> None:
        self._callback(None)
        super().reject()

    def suggestion_meta(self) -> Optional[SuggestionMetadata]:
        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
            source=(
                self.source_widget.suggestion_source()
                if self._source_needed()
                else None
            ),
        )

    def _on_change_type_changed(self) -> None:
        self._refresh_source_widget()
        self._refresh_hint_for_note_deletions()
        self._validate()

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
        self.hint_for_note_deletions.setVisible(
            self._change_type() == SuggestionType.DELETE
        )

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
    SourceType.OTHER: "",
}

# Maps source types to the placeholder text that is shown in the source input field.
# If a source type is not in this dict, no placeholder text is shown.
source_type_to_source_place_holder_text = {
    SourceType.DUPLICATE_NOTE: "[Include ID, if applicable]",
}

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
        qconnect(
            self.source_type_select.currentTextChanged, self._on_source_type_change
        )

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
        self.source_type_select.addItems(
            [source_type.value for source_type in source_types]
        )

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

        place_holder_text = source_type_to_source_place_holder_text.get(
            self._source_type(), ""
        )
        self.source_edit.setPlaceholderText(place_holder_text)

    def _source_type(self) -> Optional[SourceType]:
        if self.source_type_select.currentText():
            return SourceType(self.source_type_select.currentText())
        else:
            return None
