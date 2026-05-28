"""Dialog for creating a suggestion for a note or a bulk suggestion for multiple notes."""

import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from html import escape
from pprint import pformat
from typing import Callable, Collection, Dict, List, Mapping, Optional, Sequence, Set, Union

import aqt
from anki.models import NotetypeId
from anki.notes import Note, NoteId
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSize,
    QSizePolicy,
    QSpacerItem,
    QStyle,
    QStyleOptionButton,
    Qt,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import show_info, showInfo, showText

from .. import LOGGER
from ..ankihub_client import (
    AnkiHubHTTPError,
    SuggestionType,
)
from ..ankihub_client.models import UserDeckRelation
from ..db import ankihub_db
from ..main.suggestions import (
    ANKIHUB_EMPTY_FIRST_FIELD_ERROR,
    ANKIHUB_NO_CHANGE_ERROR,
    ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR,
    AUTO_PROTECT_FEATURE_FLAG,
    BulkNoteSuggestionsResult,
    BulkSuggestionFilters,
    ChangeSuggestionResult,
    NoteDiff,
    any_suggestible_from_diffs,
    compute_note_diffs,
    get_anki_nid_to_ah_dids_dict,
    suggest_new_note,
    suggest_note_update,
    suggest_notes_in_bulk,
)
from ..main.utils import note_type_name_without_ankihub_modifications
from ..settings import RATIONALE_FOR_CHANGE_MAX_LENGTH, config
from .errors import report_exception_and_upload_logs
from .media_sync import media_sync
from .utils import (
    active_window_or_mw,
    show_error_dialog,
    show_tooltip,
)


def _panel_background_color() -> str:
    """Background fill for the dialog's grouped panels (Include-in-suggestion + Source)."""
    return "#262626" if theme_manager.night_mode else "#ededed"


def _panel_line_color() -> str:
    """Subtle line color (section dividers, input borders) for the grouped panels.
    Light uses the Figma value; dark uses a grey that reads against the dark fill
    instead of the stark light line.
    """
    return "#4d4d4d" if theme_manager.night_mode else "#d1d5db"


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
    filters: BulkSuggestionFilters = field(default_factory=BulkSuggestionFilters)


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
    diffs = compute_note_diffs([note])
    SuggestionDialog(
        is_new_note_suggestion=ah_nid is None,
        is_for_anking_deck=ah_did == config.anking_deck_id,
        can_submit_without_review=_can_submit_without_review(ah_did=ah_did),
        added_new_media=diffs[NoteId(note.id)].added_new_media,
        callback=lambda suggestion_meta: _on_suggestion_dialog_for_single_suggestion_closed(
            suggestion_meta=suggestion_meta,
            note=note,
            ah_did=ah_did,
            parent=parent,
        ),
        notes=[note],
        note_diffs=diffs,
        ah_did=ah_did,
        preselected_change_type=preselected_change_type,
        globally_protected_fields_by_mid=_globally_protected_fields_by_mid(ah_did),
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

    per_note_filters = suggestion_meta.filters.for_mid(NotetypeId(note.mid))

    ah_nid = ankihub_db.ankihub_nid_for_anki_nid(note.id)
    if ah_nid:
        try:
            suggestion_result = suggest_note_update(
                note=note,
                change_type=suggestion_meta.change_type,
                comment=_comment_with_source(suggestion_meta),
                media_upload_cb=media_sync.start_media_upload,
                auto_accept=suggestion_meta.auto_accept,
                filters=per_note_filters,
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
            submitted = suggest_new_note(
                note=note,
                ankihub_did=ah_did,
                comment=suggestion_meta.comment,
                media_upload_cb=media_sync.start_media_upload,
                auto_accept=suggestion_meta.auto_accept,
                filters=per_note_filters,
            )
        except AnkiHubHTTPError as e:
            _handle_suggestion_error(e, parent)
            return
        if submitted:
            show_tooltip("Submitted suggestion to AnkiHub.", parent=parent)
        else:
            show_tooltip("No changes to suggest.", parent=parent)


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
    globally_protected = _globally_protected_fields_by_mid(ah_did)

    diffs = compute_note_diffs(notes)

    if config.get_feature_flags().get(AUTO_PROTECT_FEATURE_FLAG, False) and not any_suggestible_from_diffs(
        notes, diffs, preselected_change_type, globally_protected
    ):
        show_tooltip("No changes to suggest. Try syncing with AnkiHub first.", parent=parent)
        return

    SuggestionDialog(
        is_new_note_suggestion=False,
        is_for_anking_deck=ah_did == config.anking_deck_id,
        can_submit_without_review=_can_submit_without_review(ah_did=ah_did),
        added_new_media=any(d.added_new_media for d in diffs.values()),
        callback=lambda suggestion_meta: _on_suggestion_dialog_for_bulk_suggestion_closed(
            suggestion_meta=suggestion_meta,
            notes=notes,
            ah_did=ah_did,
            parent=parent,
        ),
        notes=notes,
        note_diffs=diffs,
        ah_did=ah_did,
        preselected_change_type=preselected_change_type,
        globally_protected_fields_by_mid=globally_protected,
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
            filters=suggestion_meta.filters,
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


def _globally_protected_fields_by_mid(ah_did: uuid.UUID) -> Dict[NotetypeId, Set[str]]:
    """Coerce the cached globally-protected fields into the shape the widget expects."""
    return {NotetypeId(mid): set(names) for mid, names in config.globally_protected_fields(ah_did).items()}


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
        note_diffs: Optional[Mapping[NoteId, NoteDiff]] = None,
        ah_did: Optional[uuid.UUID] = None,
        preselected_change_type: Optional[SuggestionType] = None,
        globally_protected_fields_by_mid: Optional[Mapping[NotetypeId, Collection[str]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        if parent is None:
            parent = active_window_or_mw()

        super().__init__(parent)
        # Block input to other Anki windows while the dialog is open — prevents
        # the user from editing the selected notes (or syncing) between dialog
        # open and submit, which would invalidate the "Include in suggestion"
        # selection.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._is_new_note_suggestion = is_new_note_suggestion
        self._is_for_anking_deck = is_for_anking_deck
        self._can_submit_without_review = can_submit_without_review
        self._added_new_media = added_new_media
        self._callback = callback
        self._notes = list(notes)
        self._note_diffs = note_diffs
        # Notes and diffs are paired inputs — callers always compute diffs at the same time as
        # the note list and pass both. Pinning the invariant here means downstream code (e.g.
        # the widget gate) can treat `_note_diffs` as non-None whenever `_notes` is non-empty.
        assert not self._notes or self._note_diffs is not None
        self._ah_did = ah_did
        self._fields_widget: Optional[IncludeInSuggestionWidget] = None
        self._globally_protected_by_mid: Optional[Mapping[NotetypeId, Collection[str]]] = (
            globally_protected_fields_by_mid
        )
        # Snapshot prior per-mid deselections at construction. Reused by `_save_deselections`
        # so a field deselected in an earlier session survives even when it isn't shown in
        # this session's widget.
        self._initial_deselected_by_mid: Dict[NotetypeId, Set[str]] = {}
        if self._ah_did is not None and self._notes:
            mids = {NotetypeId(n.mid) for n in self._notes}
            self._initial_deselected_by_mid = {
                mid: set(config.last_deselected_fields(self._ah_did, mid)) for mid in mids
            }

        self._setup_ui()

        if preselected_change_type:
            self.change_type_select.setCurrentText(preselected_change_type.value[1])

        self.show()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Note Suggestion(s)")
        # Without a floor, non-AnKing / new-note configurations (no Change Type
        # dropdown, no Source widget, no photo-credit hint) collapse the dialog
        # to an awkwardly short height.
        self.setMinimumHeight(480)

        # Outer vbox = [ two-column content row , button row ]. Buttons live
        # below both columns so the left frame stops at the bottom of the
        # right column's content (e.g. "Submit without review"), not at the
        # bottom of the dialog.
        outer_layout = QVBoxLayout()
        # Uniform 16px gutter: dialog margins, the gap between the two columns,
        # and the gap between the content and the button row all match.
        outer_layout.setContentsMargins(16, 16, 16, 16)
        outer_layout.setSpacing(16)
        self.setLayout(outer_layout)

        content_row = QHBoxLayout()
        content_row.setSpacing(16)
        outer_layout.addLayout(content_row, 1)

        if (
            config.get_feature_flags().get(AUTO_PROTECT_FEATURE_FLAG, False)
            and self._notes
            and self._ah_did is not None
        ):
            assert self._note_diffs is not None  # paired with `_notes`; see __init__
            self._fields_widget = IncludeInSuggestionWidget(
                notes=self._notes,
                note_diffs=self._note_diffs,
                initial_deselected_by_mid=self._initial_deselected_by_mid,
                globally_protected_fields_by_mid=self._globally_protected_by_mid,
            )
            self._fields_widget.setMinimumWidth(220)
            # Cap the left column so pathologically long section titles can't
            # push the dialog wide; the section-title checkbox elides instead.
            self._fields_widget.setMaximumWidth(360)
            content_row.addWidget(self._fields_widget, 2)
            qconnect(self._fields_widget.selection_changed, self._validate)

        right_layout = QVBoxLayout()
        content_row.addLayout(right_layout, 3)

        # Small top inset so "Change Type" doesn't sit flush against the top edge.
        right_layout.addSpacing(8)

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

        self.source_widget = SourceWidget()
        self.source_widget_group_box = QGroupBox("Source (Required)")
        self.source_widget_group_box.setObjectName("sourceGroupBox")
        # Same neutral panel background as the Include-in-suggestion section.
        self.source_widget_group_box.setStyleSheet(
            f"#sourceGroupBox {{ background-color: {_panel_background_color()}; "
            f"border: none; border-radius: 6px; margin-top: 8px; }} "
            f"#sourceGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; "
            f"left: 6px; padding: 0 3px; }}"
        )
        right_layout.addWidget(self.source_widget_group_box)
        self.source_widget_group_box_layout = QVBoxLayout()
        self.source_widget_group_box.setLayout(self.source_widget_group_box_layout)

        self.source_widget_group_box_layout.addWidget(self.source_widget)
        qconnect(self.source_widget.validation_signal, self._validate)
        right_layout.addSpacing(10)

        self.hint_for_note_deletions = QLabel("💡 When deleting a note, any changes to fields will not be applied.")
        self.hint_for_note_deletions.setWordWrap(True)
        self.hint_for_note_deletions.hide()
        right_layout.addWidget(self.hint_for_note_deletions)
        right_layout.addSpacing(10)

        right_layout.addWidget(QLabel("Rationale for Change (Required)"))

        self.rationale_edit = QPlainTextEdit()
        self.rationale_edit.setPlaceholderText(RATIONALE_FOR_CHANGE_PLACEHOLDER)
        right_layout.addWidget(self.rationale_edit)

        def limit_length():
            while len(self.rationale_edit.toPlainText()) >= RATIONALE_FOR_CHANGE_MAX_LENGTH:
                self.rationale_edit.textCursor().deletePreviousChar()

        qconnect(self.rationale_edit.textChanged, limit_length)
        qconnect(self.rationale_edit.textChanged, self._validate)

        # Configure the source widget now that rationale_edit exists — populating
        # the source-type dropdown re-validates, which reads rationale_edit.
        self._refresh_source_widget()

        right_layout.addSpacing(10)

        if self._added_new_media and self._is_for_anking_deck:
            right_layout.addWidget(
                QLabel(
                    "Please provide the source of images or audio files<br>"
                    "in the rationale field. For example:<br>"
                    "Photo credit: The AnKing [www.ankingmed.com]"
                )
            )
            right_layout.addSpacing(10)

        self.auto_accept_cb = QCheckBox("Submit without review.")
        self.auto_accept_cb.setVisible(self._can_submit_without_review)
        right_layout.addWidget(self.auto_accept_cb)

        # Button box at the dialog's bottom edge (outside the two-column row)
        # so the left frame stops at the bottom of the right column's content.
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        qconnect(self.button_box.accepted, self.accept)
        qconnect(self.button_box.rejected, self.reject)
        outer_layout.addWidget(self.button_box)

        # Visibility depends on _change_type(), so this has to run after the dropdown exists.
        if self._fields_widget is not None:
            self._refresh_fields_widget_visibility()

        self._set_submit_button_enabled_state(False)
        qconnect(self.validation_signal, self._set_submit_button_enabled_state)

    def accept(self) -> None:
        if self._fields_widget_active():
            self._save_deselections()
        self._callback(self.suggestion_meta())
        super().accept()

    def _save_deselections(self) -> None:
        """Persist the user's per-mid deselected fields. Merges with priors so a deselection
        survives sessions where the field isn't shown (not edited this round); an explicit
        re-check in this session clears that field's prior deselection.
        """
        assert self._fields_widget is not None and self._ah_did is not None
        for mid, this_session in self._fields_widget.field_selection_state_by_mid().items():
            selected = {name for name, checked in this_session.items() if checked}
            deselected = {name for name, checked in this_session.items() if not checked}
            prior = self._initial_deselected_by_mid.get(mid, set())
            merged = (prior - selected) | deselected
            config.set_last_deselected_fields(self._ah_did, mid, sorted(merged))

    def reject(self) -> None:
        self._callback(None)
        super().reject()

    def _fields_widget_active(self) -> bool:
        """The Include-in-suggestion selector exists for this dialog instance and is currently
        visible. It's constructed for both change-note and new-note flows; hidden only when the
        change type is DELETE.
        """
        return self._fields_widget is not None and self._fields_widget.isVisible()

    def suggestion_meta(self) -> Optional[SuggestionMetadata]:
        filters = self._fields_widget.suggestion_filters() if self._fields_widget_active() else BulkSuggestionFilters()
        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
            source=(self.source_widget.suggestion_source() if self._source_needed() else None),
            filters=filters,
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
            # DELETE's source (Duplicate Note) is optional, so don't claim "(Required)" there.
            title = "Source (Required)" if self.source_widget.input_is_required() else "Source"
            self.source_widget_group_box.setTitle(title)
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
        self.validation_signal.emit(self._is_valid())

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
        """Returns the user-selected change type for change-note flows, or `None` for
        new-note flows (which don't carry a change type — the server infers it).
        Callers that compare against a specific `SuggestionType` (e.g. `== DELETE`)
        silently evaluate False on the new-note branch, which is the intended behavior.
        """
        if self._is_new_note_suggestion:
            return None
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
    SourceType.OTHER: "Details",
}

# Maps source types to the placeholder text that is shown in the source input field.
# If a source type is not in this dict, no placeholder text is shown.
source_type_to_source_place_holder_text = {
    SourceType.AMBOSS: "Paste AMBOSS link",
    SourceType.UWORLD: "e.g. 12345",
    SourceType.SOCIETY_GUIDELINES: "Paste link to guidelines",
    SourceType.DUPLICATE_NOTE: "[Include ID, if applicable]",
    SourceType.OTHER: "Describe the source and include relevant details...",
}

# Placeholder shown in the Rationale-for-change textarea.
RATIONALE_FOR_CHANGE_PLACEHOLDER = "Explain why this change should be made..."

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

        # "Other" needs free-text describing the source — a taller, top-aligned,
        # line-wrapping box rather than the single-line input the other types use.
        self.source_multiline_edit = QPlainTextEdit()
        self.source_multiline_edit.setFixedHeight(60)
        self.source_multiline_edit.hide()
        qconnect(self.source_multiline_edit.textChanged, self._validate)
        self.layout_.addWidget(self.source_multiline_edit)

        # The inputs sit on the panel's neutral fill, which in dark mode is close
        # to the native input background — a border keeps them legible. Re-assert
        # background-color so the border stylesheet doesn't drop the themed fill.
        border = _panel_line_color()
        self.source_edit.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {border}; border-radius: 4px; "
            f"padding: 2px 4px; background-color: palette(base); }}"
        )
        self.source_multiline_edit.setStyleSheet(
            f"QPlainTextEdit {{ border: 1px solid {border}; border-radius: 4px; background-color: palette(base); }}"
        )

    def _source_text(self) -> str:
        if self._source_type() == SourceType.OTHER:
            return self.source_multiline_edit.toPlainText()
        return self.source_edit.text()

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
        source = self._source_text()

        if source_type == SourceType.UWORLD:
            step = self.uworld_step_select.currentText()
            source = f"{step} {source}"

        return SuggestionSource(source_type=source_type, source_text=source)

    def input_is_required(self) -> bool:
        """Whether the current source type requires the user to fill the input. DELETE's only
        source type (Duplicate Note) is optional, so callers can drop the "(Required)" label.
        """
        return self._source_type() not in source_types_where_input_is_optional

    def is_valid(self) -> bool:
        if self._source_type() in source_types_where_input_is_optional:
            return True

        return len(self._source_text().strip()) > 0

    def _validate(self) -> None:
        self.validation_signal.emit(self.is_valid())

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
        # Re-validate: switching to/from "Other" changes which input is read, so
        # the OK-button gate must refresh even though no text changed.
        self._validate()

    def _refresh_source_input_label(self) -> None:
        source_type = self._source_type()
        # The "(Required)" indicator lives on the Source group box title (set by the dialog),
        # so the inner field label stays bare to avoid showing "(Required)" twice.
        self.source_input_label.setText(source_type_to_source_label[source_type])

        # "Other" uses the multi-line box; every other source type uses the single-line input.
        place_holder_text = source_type_to_source_place_holder_text.get(source_type, "")
        uses_multiline = source_type == SourceType.OTHER
        self.source_multiline_edit.setVisible(uses_multiline)
        self.source_edit.setVisible(not uses_multiline)
        active_input = self.source_multiline_edit if uses_multiline else self.source_edit
        active_input.setPlaceholderText(place_holder_text)

    def _source_type(self) -> Optional[SourceType]:
        if self.source_type_select.currentText():
            return SourceType(self.source_type_select.currentText())
        else:
            return None


@lru_cache(maxsize=1)
def _native_checkbox_text_offset() -> int:
    """The x where a native QCheckBox draws its label text (indicator width +
    style label-spacing + margins). Used to align a label-only checkbox row's
    text with native QCheckBoxes elsewhere in the panel.

    Cached: the value is constant per style/session, and a bulk suggestion can
    build hundreds of tag rows.
    """
    probe = QCheckBox("x")
    probe.resize(probe.sizeHint())
    opt = QStyleOptionButton()
    opt.initFrom(probe)
    opt.text = "x"
    opt.rect = probe.rect()
    return probe.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents, opt, probe).x()


class _TagLabel(QLabel):
    """A QLabel for tag-name display.

    Centralizes tag rendering: elides long tags to `…::leaf` (or `<head>…` for
    flat names), injects zero-width spaces after `::` and `_` so `wordWrap`
    has break points inside identifier-style names, and sets a (rich-text)
    tooltip with the full tag — only when actually elided. Emits `clicked`
    on mouse press so a paired checkbox can toggle.
    """

    clicked = pyqtSignal()

    _MAX_LEN = 85

    def __init__(self, tag: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        display = self._elide(tag)
        self.setText(self._inject_breakpoints(display))
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # Wrapping a label inside a layout takes both: a horizontal policy that
        # lets the layout pick the width (else sizeHint is the unwrapped single
        # line) and `setHeightForWidth(True)` on the policy so the layout asks
        # the label "how tall do you want to be at this width?" Without the
        # latter, wordWrap=True wraps the *paint* but vertical sizing stays at
        # a single line.
        sp = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumWidth(1)
        # Signal clickability on hover, matching the native checkbox labels.
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if display != tag:
            self.setToolTip(f"<p>{self._inject_breakpoints(escape(tag))}</p>")

    @classmethod
    def _elide(cls, tag: str) -> str:
        """Tags shorter than the budget render as-is. Longer hierarchical tags
        collapse to `…::leaf` (the trailing `::`-segment with an ellipsis
        prefix). The leaf itself is truncated if it would still exceed the
        budget. Long flat tags get a plain `…` truncation.
        """
        if len(tag) <= cls._MAX_LEN:
            return tag
        if "::" in tag:
            leaf = tag.rsplit("::", 1)[-1]
            leaf_budget = cls._MAX_LEN - 3  # account for "…::" prefix
            if len(leaf) > leaf_budget:
                leaf = f"{leaf[: leaf_budget - 1]}…"
            return f"…::{leaf}"
        return f"{tag[: cls._MAX_LEN - 1]}…"

    @staticmethod
    def _inject_breakpoints(text: str) -> str:
        """Insert zero-width spaces after `::` and `_` so QLabel.wordWrap has
        valid break points inside identifier-style names (`a::b::c_d`).
        """
        return text.replace("::", "::​").replace("_", "_​")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mousePressEvent(event)
        self.clicked.emit()


class _TagCheckBox(QWidget):
    """A QCheckBox-like row pairing a checkbox indicator with a `_TagLabel`
    whose tag name wraps to multiple lines.

    QCheckBox draws its label inline as a single line; long tag names overflow
    the section width and force a horizontal scrollbar. This widget keeps the
    same `.isChecked() / .setChecked() / .toggled` API the rest of the code
    expects but pairs the indicator with a wrappable `_TagLabel`.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, tag: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.setLayout(row)

        self._checkbox = QCheckBox()
        # Pad the (text-less) checkbox out to the exact x where a native QCheckBox
        # draws its text, then drop the row spacing to zero — so the tag label
        # lines up with the section title and field items (which are native
        # QCheckBoxes) instead of sitting a few px further right.
        self._content_offset = _native_checkbox_text_offset()
        self._checkbox.setFixedWidth(self._content_offset)
        row.setSpacing(0)
        row.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignTop)

        self._label = _TagLabel(tag)
        qconnect(self._label.clicked, self._checkbox.toggle)
        row.addWidget(self._label, 1)

        qconnect(self._checkbox.toggled, self.toggled.emit)

    # Propagate heightForWidth from the inner label up to this widget so the
    # parent QVBoxLayout grows the row vertically when the label wraps.
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        # The label starts at exactly `_content_offset` because the checkbox is
        # `setFixedWidth(_content_offset)` with zero row spacing — keep those in
        # sync if either changes.
        label_width = max(1, width - self._content_offset)
        label_height = self._label.heightForWidth(label_width)
        # Don't drop below the bare checkbox height (single-line fallback).
        return max(label_height, self._checkbox.sizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        hint_w = self._checkbox.sizeHint().width()  # ignore label's natural width
        hint_h = self._checkbox.sizeHint().height()
        return QSize(hint_w, hint_h)

    # QCheckBox-compatible surface used by `_GroupController` and the
    # `IncludeInSuggestionWidget` accessors.
    def isChecked(self) -> bool:  # noqa: N802 - Qt-style name
        return self._checkbox.isChecked()

    def setChecked(self, value: bool) -> None:  # noqa: N802
        self._checkbox.setChecked(value)


class _RowCheckBox(QCheckBox):
    """A QCheckBox whose entire (stretched) width is the click target.

    Qt's default `hitButton` only accepts clicks over the indicator + label,
    so a full-width checkbox shows the hover cursor across the whole row but
    only toggles on the text. Accepting the full rect makes the click area
    match the cursor area (consistent with the wrapping tag rows).
    """

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt override
        return self.rect().contains(pos)


class _SelectAllCheckBox(_RowCheckBox):
    """Displays PartiallyChecked programmatically, but user clicks only toggle
    Checked ↔ Unchecked — Qt's default Unchecked → PartiallyChecked cycle would
    block "select all" from an empty group.

    Elides its label dynamically: the displayed text shrinks to fit the
    checkbox's actual width via QFontMetrics. When elided, the full text
    shows up as a tooltip.
    """

    def __init__(self, full_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self._full_text = full_text
        # Without Ignored, the checkbox's sizeHint claims the full text width
        # and the parent layout grows to fit — elision never kicks in.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(1)
        self._elide_to_fit()

    def nextCheckState(self) -> None:  # noqa: N802 - Qt override
        if self.checkState() == Qt.CheckState.Checked:
            self.setCheckState(Qt.CheckState.Unchecked)
        else:
            self.setCheckState(Qt.CheckState.Checked)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._elide_to_fit()

    def _elide_to_fit(self) -> None:
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        style = self.style()
        indicator = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, opt, self).width()
        spacing = style.pixelMetric(QStyle.PixelMetric.PM_CheckBoxLabelSpacing, opt, self)
        avail = self.width() - indicator - spacing - 4  # small safety margin

        if avail <= 0:
            text = self._full_text
        else:
            text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail)

        # Skip setText if unchanged to avoid resize-event loops.
        if text != self.text():
            super().setText(text)
        self.setToolTip(self._full_text if text != self._full_text else "")


_Toggleable = Union[QCheckBox, _TagCheckBox]


class _GroupController:
    """Wires a Select-all parent checkbox to its child checkboxes with tri-state
    feedback: parent reflects PartiallyChecked when children are mixed; toggling
    the parent sets every child to the new state.
    """

    def __init__(
        self,
        parent_cb: QCheckBox,
        on_child_toggle: Callable[[], None],
    ) -> None:
        self._parent = parent_cb
        self._children: List[_Toggleable] = []
        self._on_child_toggle = on_child_toggle
        self._suppress = False
        self._parent.setTristate(True)
        qconnect(self._parent.clicked, self._on_parent_clicked)

    def add_child(self, cb: _Toggleable) -> None:
        self._children.append(cb)
        qconnect(cb.toggled, self._on_child_toggled)

    def refresh_parent(self) -> None:
        # Disabled children are locked (always-checked) — they shouldn't drive
        # parent tri-state or be touched by Select-all.
        togglable = [c for c in self._children if c.isEnabled()]
        if not togglable:
            # Nothing for the (full-row clickable) Select-all to toggle; disable
            # it and show it checked rather than letting it flip with no effect.
            self._suppress = True
            self._parent.setCheckState(Qt.CheckState.Checked)
            self._suppress = False
            self._parent.setEnabled(False)
            return
        selected = sum(1 for c in togglable if c.isChecked())
        self._suppress = True
        if selected == 0:
            self._parent.setCheckState(Qt.CheckState.Unchecked)
        elif selected == len(togglable):
            self._parent.setCheckState(Qt.CheckState.Checked)
        else:
            self._parent.setCheckState(Qt.CheckState.PartiallyChecked)
        self._suppress = False

    def _on_parent_clicked(self, _checked: bool) -> None:
        # `clicked` (vs `toggled`) only fires on user interaction, so we don't
        # need to guard against the programmatic setCheckState calls in
        # refresh_parent. After a user click, Qt advances the tri-state cycle
        # — interpret anything other than "now Checked" as "set all off".
        target = self._parent.checkState() == Qt.CheckState.Checked
        self._suppress = True
        for c in self._children:
            if c.isEnabled():
                c.setChecked(target)
        self._suppress = False
        self.refresh_parent()
        self._on_child_toggle()

    def _on_child_toggled(self, _checked: bool) -> None:
        if self._suppress:
            return
        self.refresh_parent()
        self._on_child_toggle()


_FIRST_FIELD_LOCK_TOOLTIP = "The first field is required for new-note suggestions."


class IncludeInSuggestionWidget(QWidget):
    """Selector for which edited fields and tag changes to include in the
    outgoing suggestion. Shown for change-note and new-note flows; hidden
    for DELETE.

    Layout: one group per note type for fields (single and bulk), plus
    "Added Tags" / "Removed Tags" sections. Each group has a tri-state
    Select-all checkbox. Globally-protected fields are excluded entirely —
    not listed in the widget and not sent in the suggestion.
    """

    selection_changed = pyqtSignal()

    def __init__(
        self,
        notes: Sequence[Note],
        note_diffs: Mapping[NoteId, NoteDiff],
        initial_deselected_by_mid: Optional[Mapping[NotetypeId, Collection[str]]] = None,
        globally_protected_fields_by_mid: Optional[Mapping[NotetypeId, Collection[str]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._notes = list(notes)
        self._note_diffs = note_diffs
        self._globally_protected: Dict[NotetypeId, Set[str]] = {
            mid: set(names) for mid, names in (globally_protected_fields_by_mid or {}).items()
        }
        self._field_checkboxes: Dict[NotetypeId, Dict[str, QCheckBox]] = {}
        self._added_tag_boxes: Dict[str, QCheckBox] = {}
        self._removed_tag_boxes: Dict[str, QCheckBox] = {}
        self._setup_ui(initial_deselected_by_mid or {})

    def _setup_ui(self, initial_deselected_by_mid: Mapping[NotetypeId, Collection[str]]) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        self.setLayout(outer)

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        frame.setObjectName("includeInSuggestionFrame")
        frame.setStyleSheet(
            f"#includeInSuggestionFrame {{ background-color: {_panel_background_color()}; border-radius: 6px; }}"
        )
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        outer.addWidget(frame)

        frame_layout = QVBoxLayout()
        frame_layout.setContentsMargins(14, 14, 14, 14)
        frame_layout.setSpacing(10)
        frame.setLayout(frame_layout)

        title_row = QHBoxLayout()
        self._title_label = QLabel("<b>Include in suggestion</b>")
        self._counter_label = QLabel("")
        counter_font = self._counter_label.font()
        counter_font.setPointSize(max(counter_font.pointSize() - 2, 8))
        self._counter_label.setFont(counter_font)
        self._counter_label.setStyleSheet("color: palette(placeholder-text);")
        title_row.addWidget(self._title_label)
        title_row.addStretch()
        title_row.addWidget(self._counter_label)
        frame_layout.addLayout(title_row)

        subtitle = QLabel("Select which fields you want to submit changes for")
        subtitle.setStyleSheet("color: palette(placeholder-text);")
        subtitle.setWordWrap(True)
        frame_layout.addWidget(subtitle)
        frame_layout.addSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setMinimumHeight(120)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        body = QWidget()
        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(8)
        body.setLayout(self._body_layout)
        self._scroll.setWidget(body)
        frame_layout.addWidget(self._scroll)

        self._populate(initial_deselected_by_mid)
        self._refresh_counter()

    def _add_section(
        self,
        title: str,
        items: List[str],
        initial_checked: Dict[str, bool],
        lock_first_field: Optional[str] = None,
        item_widget: Callable[[str], "_Toggleable"] = _RowCheckBox,
    ) -> Dict[str, QCheckBox]:
        """Render one section: select-all checkbox (with the section title as
        its label) + one child checkbox per item, then a thin separator below.

        `lock_first_field`, if set, names the item to render as checked +
        disabled (skipped by Select-all) — used to lock the first field for
        new-note suggestions.
        `item_widget` is a factory that builds the per-item checkbox from the
        item name. Defaults to plain `QCheckBox`; `_add_tag_section` passes
        `_TagCheckBox` so tag names wrap and self-tooltip when elided.
        Returns `item -> checkbox` so the caller can wire up persistence.
        """
        if self._body_layout.count() > 0:
            separator = QFrame()
            separator.setFixedHeight(1)
            separator.setStyleSheet(f"background-color: {_panel_line_color()}; border: none;")
            self._body_layout.addWidget(separator)

        # Title sits on the select-all checkbox itself, so clicking the title
        # toggles the section and keeps the label aligned at the same x as the
        # child item checkboxes. Pass `full_text` so the label elides
        # dynamically to fit the checkbox's actual width (tooltip shows the
        # full string when elided).
        select_all = _SelectAllCheckBox(title)
        select_all.setStyleSheet("QCheckBox { color: palette(placeholder-text); font-weight: bold; }")
        self._body_layout.addWidget(select_all)

        controller = _GroupController(select_all, self._on_toggle)
        # Anchor the controller to a Qt-owned widget — without this Qt drops
        # the bound-method signal connections when the Python ref is GC'd.
        select_all._group_controller_ref = controller  # type: ignore[attr-defined]

        # No indent — all checkboxes (section select-alls + items) align at the same x.
        items_layout = QVBoxLayout()
        items_layout.setContentsMargins(0, 0, 0, 0)
        items_layout.setSpacing(2)
        self._body_layout.addLayout(items_layout)

        result: Dict[str, QCheckBox] = {}
        for item in items:
            cb = item_widget(item)
            if lock_first_field is not None and item == lock_first_field:
                cb.setChecked(True)
                cb.setEnabled(False)
                cb.setToolTip(_FIRST_FIELD_LOCK_TOOLTIP)
            else:
                cb.setChecked(initial_checked.get(item, True))
            controller.add_child(cb)
            # Full width for every row: `_RowCheckBox` accepts clicks across its
            # whole width and `_TagCheckBox`'s label wraps + is fully clickable, so
            # the click area matches the hover cursor consistently.
            items_layout.addWidget(cb)
            result[item] = cb  # type: ignore[assignment]
        controller.refresh_parent()
        return result

    def _populate(self, initial_deselected_by_mid: Mapping[NotetypeId, Collection[str]]) -> None:
        fields_by_mid: Dict[NotetypeId, List[str]] = {}
        note_type_name_by_mid: Dict[NotetypeId, str] = {}
        # The first field is required for new-note suggestions (server-side
        # validation rejects otherwise). Lock the checkbox for any mid that
        # has at least one new-note candidate in the batch.
        locked_first_field_by_mid: Dict[NotetypeId, str] = {}
        added_tags: List[str] = []
        removed_tags: List[str] = []
        for note in self._notes:
            mid = NotetypeId(note.mid)
            note_type_name_by_mid.setdefault(mid, note.note_type()["name"])
            diff = self._note_diffs[NoteId(note.id)]
            globally_protected = self._globally_protected.get(mid, set())
            fields = [f for f in diff.edited_fields if f not in globally_protected]
            # `dict.fromkeys(...)` dedupes across notes sharing this mid while preserving
            # first-seen field order so the widget renders fields in note-type definition order.
            fields_by_mid[mid] = list(dict.fromkeys((*fields_by_mid.get(mid, ()), *fields)))
            added_tags.extend(diff.added_tags)
            removed_tags.extend(diff.removed_tags)
            if not diff.exists_in_ah_db and mid not in locked_first_field_by_mid:
                locked_first_field_by_mid[mid] = note.note_type()["flds"][0]["name"]

        for mid, fields in fields_by_mid.items():
            if not fields:
                continue
            deselected = set(initial_deselected_by_mid.get(mid, ()))
            clean_name = note_type_name_without_ankihub_modifications(note_type_name_by_mid[mid])
            first_field = locked_first_field_by_mid.get(mid)
            lock_first_field = first_field if (first_field is not None and first_field in fields) else None
            self._field_checkboxes[mid] = self._add_section(
                title=clean_name,
                items=fields,
                initial_checked={f: f not in deselected for f in fields},
                lock_first_field=lock_first_field,
            )

        if added_tags:
            self._added_tag_boxes = self._add_tag_section("Added Tags", sorted(set(added_tags)))
        if removed_tags:
            self._removed_tag_boxes = self._add_tag_section("Removed Tags", sorted(set(removed_tags)))

        self._body_layout.addStretch()

    def _add_tag_section(self, title: str, tags: List[str]) -> Dict[str, QCheckBox]:
        return self._add_section(
            title=title,
            items=tags,
            initial_checked={t: True for t in tags},
            item_widget=_TagCheckBox,
        )

    def _on_toggle(self) -> None:
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
        self._counter_label.setText(f"{selected}/{total}" if total else "")

    def has_any_selection(self) -> bool:
        return any(cb.isChecked() for cb in self._all_checkboxes())

    def suggestion_filters(self) -> BulkSuggestionFilters:
        return BulkSuggestionFilters(
            fields_to_include_by_mid=self.selected_field_names_by_mid(),
            tags_to_add=self.selected_tag_additions(),
            tags_to_remove=self.selected_tag_removals(),
        )

    def selected_field_names_by_mid(self) -> Dict[NotetypeId, List[str]]:
        return {
            mid: [name for name, cb in mid_map.items() if cb.isChecked()]
            for mid, mid_map in self._field_checkboxes.items()
        }

    def field_selection_state_by_mid(self) -> Dict[NotetypeId, Dict[str, bool]]:
        """Per-mid `{field_name: is_checked}` for every field the widget rendered. Used by the
        dialog to merge "this session's deselections" with priors from earlier sessions.
        """
        return {
            mid: {name: cb.isChecked() for name, cb in mid_map.items()}
            for mid, mid_map in self._field_checkboxes.items()
        }

    def selected_tag_additions(self) -> List[str]:
        return [tag for tag, cb in self._added_tag_boxes.items() if cb.isChecked()]

    def selected_tag_removals(self) -> List[str]:
        return [tag for tag, cb in self._removed_tag_boxes.items() if cb.isChecked()]
