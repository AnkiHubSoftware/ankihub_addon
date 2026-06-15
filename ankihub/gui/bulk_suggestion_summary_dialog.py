"""Dialog summarizing the result of a bulk note suggestion submit, including the
interactive "Notes already in this deck" action that resubmits new-note suggestions
as change suggestions for the notes that already exist on AnkiHub."""

import uuid
from concurrent.futures import Future
from enum import Enum
from html import escape
from typing import Dict, List, Mapping, Optional, Tuple

import aqt
from anki.notes import NoteId
from aqt.qt import (
    QCloseEvent,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import openLink

from .. import LOGGER
from ..ankihub_client import AnkiHubHTTPError, SuggestionType
from ..main.suggestions import (
    ANKIHUB_EMPTY_FIRST_FIELD_ERROR,
    ANKIHUB_NO_CHANGE_ERROR,
    ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR,
    AlreadyInDeckConflict,
    BulkNoteSuggestionsResult,
    resubmit_new_notes_as_change_suggestions_in_bulk,
)
from .utils import clear_layout, show_error_dialog, show_tooltip

SUPPORT_FORUM_URL = "https://community.ankihub.net/c/support"
DELETED_NOTES_DOCS_URL = "https://community.ankihub.net/t/deleting-notes/170582"
MUTED_TEXT_COLOR = "#9aa0a6"

# Issue categories shown under "Submission issues", in display order:
# (key, label, error substring, guidance HTML).
# "No changes" is surfaced separately as a skipped-count line in the Summary, and
# "other_errors" is the catch-all appended after these.
BULK_ISSUE_CATEGORIES = [
    (
        "notes_deleted",
        "Notes deleted on AnkiHub",
        ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR,
        "These notes are no longer linked to AnkiHub. Keep as personal notes or "
        f'remove from your collection. <a href="{DELETED_NOTES_DOCS_URL}">Learn more</a>.',
    ),
    (
        "empty_first_field",
        "First field empty",
        ANKIHUB_EMPTY_FIRST_FIELD_ERROR,
        "Fill in the first field and try again.",
    ),
]
OTHER_ERRORS_GUIDANCE = f'Try again or <a href="{SUPPORT_FORUM_URL}">Contact support</a> if the issue persists.'


def _summary_color(positive: bool) -> str:
    if positive:
        return "#5fb16a" if theme_manager.night_mode else "#3b7c44"
    return "#e2685a" if theme_manager.night_mode else "#cc4131"


def _updated_badge_colors() -> Tuple[str, str]:
    # (background, foreground)
    return ("#0c3a1f", "#7fd39b") if theme_manager.night_mode else ("#dcfce7", "#066934")


def _action_band_bg() -> str:
    # A touch lighter than the dark window bg (raised-panel feel) / the Figma gray on light.
    return "#383838" if theme_manager.night_mode else "#e7e7e7"


def _divider_color() -> str:
    return "#4d4d4d" if theme_manager.night_mode else "#d0d4da"


class _ActionState(Enum):
    """State of the 'Notes already in this deck' action box."""

    DEFAULT = "default"
    LOADING = "loading"  # resubmit in flight
    IGNORED = "ignored"  # user chose to ignore
    RESOLVED = "resolved"  # resubmit finished (fully or partially)
    FAILED = "failed"  # resubmit hit a hard error; user may retry and can close


class BulkSuggestionSummaryDialog(QDialog):
    """Summary shown after every bulk suggestion submit. Replaces the old plain-text
    `showText` summary: a Summary section, categorized submission issues, and an
    interactive "Notes already in this deck" action that resubmits as change
    suggestions."""

    _SIDE_MARGIN = 28  # horizontal inset for every section except the full-bleed band
    _SECTION_GAP = 20  # default vertical gap between sections

    def __init__(
        self,
        result: BulkNoteSuggestionsResult,
        ah_did: uuid.UUID,
        auto_accept: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHub | Bulk Suggestion Summary")
        self.setMinimumWidth(440)
        # Application-modal (not window-modal) so macOS shows it as a normal centered
        # dialog rather than a sheet sliding out of the parent window's title bar.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._errors_by_nid: Dict[NoteId, object] = dict(result.errors_by_nid)
        self._already_in_deck: Dict[NoteId, AlreadyInDeckConflict] = dict(result.already_in_deck_by_nid)
        self._change_submitted = result.change_note_suggestions_count
        self._new_submitted = result.new_note_suggestions_count
        self._ah_did = ah_did
        self._auto_accept = auto_accept
        self._action_state = _ActionState.DEFAULT
        # Summary/category keys to flag with an "Updated" badge after a resubmit.
        self._updated_keys: set = set()

        # Side margins are 0 so the "action required" band can span the full dialog
        # width; every other section is wrapped in a SIDE_MARGIN inset via `_inset`.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 16, 0, 14)
        # Gap between the content (its last section is the action band) and the footer.
        outer.setSpacing(16)

        # Rebuilt on each state change. Category count is bounded and IDs live behind
        # "Copy note IDs" buttons, so the dialog stays a sensible size without a scroll.
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        # Spacing is added explicitly per-gap in `_render` (Qt layouts only support a
        # single uniform spacing, but a couple of gaps need bespoke values).
        self._content_layout.setSpacing(0)
        outer.addLayout(self._content_layout, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(self._SIDE_MARGIN, 0, self._SIDE_MARGIN, 0)
        footer.addStretch(1)
        self._close_button = QPushButton("Close")
        qconnect(self._close_button.clicked, self.accept)
        footer.addWidget(self._close_button)
        outer.addLayout(footer)

        if self._already_in_deck:
            LOGGER.info(
                "bulk_duplicate_note_error_dialog_shown",
                ah_did=str(self._ah_did),
                count=len(self._already_in_deck),
            )

        self._render()

    # --- state ------------------------------------------------------------
    def _can_close(self) -> bool:
        if self._action_state == _ActionState.LOADING:
            return False
        if self._action_state in (_ActionState.IGNORED, _ActionState.RESOLVED, _ActionState.FAILED):
            return True
        return not self._already_in_deck

    # Gate the window-X / Escape close (which call reject()), not just the Close
    # button, so an unresolved action or an in-flight resubmit can't be dismissed.
    def reject(self) -> None:
        if self._can_close():
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._can_close():
            super().closeEvent(event)
        else:
            event.ignore()

    def _skipped_nids(self) -> List[NoteId]:
        # Notes that had nothing to submit — surfaced as "skipped" in the Summary, not
        # as a failure.
        return [nid for nid, errors in self._errors_by_nid.items() if ANKIHUB_NO_CHANGE_ERROR in str(errors)]

    def _failed_count(self) -> int:
        return len(self._errors_by_nid) - len(self._skipped_nids())

    def _issue_categories(self) -> Dict[str, List[NoteId]]:
        """Failed nids grouped for the 'Submission issues' section. Excludes the
        skipped (no-change) notes and the resubmittable 'already in this deck' notes."""
        already = set(self._already_in_deck.keys())
        cats: Dict[str, List[NoteId]] = {key: [] for key, *_ in BULK_ISSUE_CATEGORIES}
        cats["other_errors"] = []
        for nid, errors in self._errors_by_nid.items():
            if nid in already:
                continue
            error_text = str(errors)
            if ANKIHUB_NO_CHANGE_ERROR in error_text:
                continue  # surfaced as "skipped" in the Summary
            for key, _, substring, _ in BULK_ISSUE_CATEGORIES:
                if substring in error_text:
                    cats[key].append(nid)
                    break
            else:
                cats["other_errors"].append(nid)
        return cats

    # --- rendering --------------------------------------------------------
    def _inset(self, widget: QWidget) -> QWidget:
        """Wrap a widget in a SIDE_MARGIN horizontal inset, so sections line up while
        the full-bleed action band (added un-inset) spans the whole dialog width."""
        container = QWidget()
        container.setStyleSheet(".QWidget { background: transparent; }")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(self._SIDE_MARGIN, 0, self._SIDE_MARGIN, 0)
        layout.addWidget(widget)
        return container

    def _render(self) -> None:
        clear_layout(self._content_layout)
        self._content_layout.addWidget(self._inset(self._build_summary()))

        issue_blocks = self._issue_blocks()
        if issue_blocks:
            self._content_layout.addSpacing(self._SECTION_GAP)
            self._content_layout.addWidget(self._inset(self._divider()))
            self._content_layout.addSpacing(self._SECTION_GAP)
            self._content_layout.addWidget(self._inset(self._section_header("Submission issues")))
            for key, label, guidance, nids in issue_blocks:
                self._content_layout.addSpacing(self._SECTION_GAP)
                self._content_layout.addWidget(self._inset(self._issue_block(key, label, guidance, nids)))

        if self._already_in_deck:
            self._content_layout.addSpacing(self._SECTION_GAP)
            self._content_layout.addWidget(self._inset(self._section_header("Action required", size=14)))
            self._content_layout.addSpacing(8)  # "Action required" → band
            self._content_layout.addWidget(self._build_action_band())  # full-bleed

        self._close_button.setEnabled(self._can_close())
        # Resize on the next event-loop tick: computing the fit synchronously here
        # (right after rebuilding the layout) can under-size the dialog because the
        # freshly-created widgets haven't reported their final size hints yet, which
        # squashes multi-line rows. Deferring lets the layout settle first.
        QTimer.singleShot(0, self._fit_to_content)

    def _fit_to_content(self) -> None:
        self._content_layout.activate()
        # Word-wrap labels under-report their height to adjustSize() (their sizeHint
        # assumes a wider, fewer-line layout than the dialog's actual width), which
        # clips the last line. Pin each one to its real wrapped height at the current
        # width so adjustSize() reserves enough room.
        for label in self.findChildren(QLabel):
            if label.wordWrap() and label.width() > 0:
                label.setMinimumHeight(label.heightForWidth(label.width()))
        self.adjustSize()

    def _issue_blocks(self) -> List[Tuple[str, str, str, List[NoteId]]]:
        cats = self._issue_categories()
        blocks = [(key, label, guidance, cats[key]) for key, label, _, guidance in BULK_ISSUE_CATEGORIES if cats[key]]
        if cats["other_errors"]:
            blocks.append(("other_errors", "Other errors", OTHER_ERRORS_GUIDANCE, cats["other_errors"]))
        return blocks

    def _section_header(self, text: str, size: int = 16) -> QLabel:
        # Hierarchy: Summary (18) > Submission issues (16) > Action required, issue
        # category titles, and the action-band title (all 14), all weight 700.
        label = QLabel(text)
        label.setStyleSheet(f"font-weight:700; font-size:{size}px;")
        return label

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        color = _divider_color()
        line.setStyleSheet(f"color:{color}; background:{color}; max-height:1px; border:none;")
        return line

    def _badge(self) -> QLabel:
        background, foreground = _updated_badge_colors()
        badge = QLabel("Updated")
        badge.setStyleSheet(
            f"background:{background}; color:{foreground}; border-radius:9px; "
            "padding:2px 9px; font-weight:400; font-size:12px;"
        )
        return badge

    def _copy_nids_button(self, nids: List[NoteId]) -> QPushButton:
        button = QPushButton("Copy note IDs")
        qconnect(button.clicked, lambda: self._copy_nids(nids))
        return button

    def _copy_nids(self, nids: List[NoteId]) -> None:
        search = "nid:" + ",".join(str(nid) for nid in nids)
        aqt.mw.app.clipboard().setText(search)
        show_tooltip("Copied note IDs", parent=self)

    def _guidance_label(self, html: str) -> QLabel:
        label = QLabel(html)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        qconnect(label.linkActivated, openLink)
        return label

    def _bullet(self, number: int, label: str, color: Optional[str], updated_key: Optional[str] = None) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(".QWidget { background: transparent; }")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        text = f"•&nbsp;&nbsp;<b>{number}</b> {escape(label)}"
        if color:
            text = f'<span style="color:{color};">{text}</span>'
        line = QLabel(text)
        line.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(line)
        if updated_key and updated_key in self._updated_keys:
            row.addWidget(self._badge())
        row.addStretch(1)
        return widget

    def _build_summary(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(".QWidget { background: transparent; }")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(8)

        vbox.addWidget(self._section_header("Summary", size=18))
        vbox.addWidget(
            self._bullet(
                self._change_submitted,
                "change note suggestion(s) submitted.",
                _summary_color(True),
                updated_key="change_submitted",
            )
        )
        vbox.addWidget(self._bullet(self._new_submitted, "new note suggestion(s) submitted.", None))

        skipped = self._skipped_nids()
        if skipped:
            vbox.addWidget(self._skipped_row(skipped))

        vbox.addWidget(self._bullet(self._failed_count(), "failed to submit.", _summary_color(False)))
        return container

    def _skipped_row(self, nids: List[NoteId]) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(".QWidget { background: transparent; }")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        bullet = QLabel(f"•&nbsp;&nbsp;<b>{len(nids)}</b> notes skipped")
        bullet.setTextFormat(Qt.TextFormat.RichText)
        left.addWidget(bullet)
        sub = QLabel("No changes were detected.")
        sub.setStyleSheet("margin-left:18px;")
        left.addWidget(sub)
        row.addLayout(left)

        if "skipped" in self._updated_keys:
            # AlignTop so the badge stays pill-height (lines up with the count) instead
            # of stretching to fill the two-line row.
            row.addWidget(self._badge(), alignment=Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        row.addWidget(self._copy_nids_button(nids), alignment=Qt.AlignmentFlag.AlignTop)
        return widget

    def _issue_block(self, key: str, label: str, guidance_html: str, nids: List[NoteId]) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet(".QWidget { background: transparent; }")
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = self._section_header(f"({len(nids)}) {label}", size=14)
        title_row.addWidget(title)
        if key in self._updated_keys:
            title_row.addWidget(self._badge())
        title_row.addStretch(1)
        title_row.addWidget(self._copy_nids_button(nids))
        vbox.addLayout(title_row)

        vbox.addWidget(self._guidance_label(guidance_html))
        return widget

    def _build_action_band(self) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        frame.setObjectName("actionBand")
        # Full-bleed: spans the dialog width, so no border-radius (square edges meet the sides).
        frame.setStyleSheet(f"#actionBand {{ background-color: {_action_band_bg()}; }}")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(self._SIDE_MARGIN, 12, self._SIDE_MARGIN, 12)
        vbox.setSpacing(8)
        nids = list(self._already_in_deck.keys())

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = self._section_header(f"({len(nids)}) Notes already in this deck", size=14)
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self._copy_nids_button(nids))
        vbox.addLayout(title_row)

        description = QLabel(
            "These notes already exist in this deck on AnkiHub, so you can't submit them as "
            "New Note Suggestions. Resubmit to push your edits through as Change Suggestions."
        )
        description.setWordWrap(True)
        vbox.addWidget(description)

        if self._action_state == _ActionState.IGNORED:
            ignored = QLabel("Ignored — no action taken.")
            ignored.setStyleSheet(f"color:{MUTED_TEXT_COLOR}; font-style:italic;")
            vbox.addWidget(ignored)
            return frame

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        loading = self._action_state == _ActionState.LOADING

        ignore_button = QPushButton("Ignore")
        ignore_button.setEnabled(not loading)
        qconnect(ignore_button.clicked, self._on_ignore)
        button_row.addWidget(ignore_button)

        if loading:
            submitting = QPushButton("Submitting…")
            submitting.setEnabled(False)
            button_row.addWidget(submitting)
        else:
            resubmit = QPushButton(f"({len(nids)}) Resubmit as change suggestions")
            resubmit.setDefault(True)
            qconnect(resubmit.clicked, self._on_resubmit)
            button_row.addWidget(resubmit)
        button_row.addStretch(1)
        vbox.addLayout(button_row)
        return frame

    # --- actions ----------------------------------------------------------
    def _on_ignore(self) -> None:
        self._action_state = _ActionState.IGNORED
        self._render()

    def _on_resubmit(self) -> None:
        LOGGER.info(
            "bulk_duplicate_note_resubmitted_as_update_suggestion",
            ah_did=str(self._ah_did),
            count=len(self._already_in_deck),
        )
        conflicts = dict(self._already_in_deck)
        self._action_state = _ActionState.LOADING
        self._render()

        def task() -> Dict[NoteId, object]:
            # The resubmitted notes went out as *new* notes, so no user-picked change
            # type applies; mark them as "Updated content", same as the single-note path.
            return resubmit_new_notes_as_change_suggestions_in_bulk(
                conflicts, SuggestionType.UPDATED_CONTENT, self._auto_accept
            )

        def on_done(future: Future) -> None:
            try:
                errors_by_nid = future.result()
            except Exception as e:  # hard failure (network etc.) — never trap the user
                # FAILED keeps the action buttons (so the user can retry) but also
                # lets Close work, so a failed resubmit can't trap them.
                self._action_state = _ActionState.FAILED
                self._render()
                show_error_dialog(
                    "Couldn't resubmit the suggestions. Please try again.",
                    title="Error resubmitting suggestions :(",
                    parent=self,
                )
                LOGGER.warning("Bulk resubmit failed.", exc_info=e)
                return
            self._apply_resubmit_result(conflicts, errors_by_nid)

        aqt.mw.taskman.with_progress(task=task, on_done=on_done, parent=self)

    def _apply_resubmit_result(
        self,
        conflicts: Mapping[NoteId, AlreadyInDeckConflict],
        errors_by_nid: Mapping[NoteId, object],
    ) -> None:
        succeeded = [nid for nid in conflicts if nid not in errors_by_nid]
        failed = {nid: errors_by_nid[nid] for nid in conflicts if nid in errors_by_nid}

        for nid in succeeded:
            self._errors_by_nid.pop(nid, None)
        if succeeded:
            self._change_submitted += len(succeeded)
            self._updated_keys.add("change_submitted")

        for nid, error in failed.items():
            self._errors_by_nid[nid] = error
            self._updated_keys.add(self._category_key_for(str(error)))

        self._already_in_deck = {}
        self._action_state = _ActionState.RESOLVED
        self._render()

    @staticmethod
    def _category_key_for(error_text: str) -> str:
        """Which summary/category key a re-failed note now lands in (for the badge)."""
        if ANKIHUB_NO_CHANGE_ERROR in error_text:
            return "skipped"
        for key, _, substring, _ in BULK_ISSUE_CATEGORIES:
            if substring in error_text:
                return key
        return "other_errors"


def _on_suggest_notes_in_bulk_done(
    future: Future,
    parent: QWidget,
    ah_did: uuid.UUID,
    auto_accept: bool,
) -> None:
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

    BulkSuggestionSummaryDialog(
        result=suggestions_result,
        ah_did=ah_did,
        auto_accept=auto_accept,
        parent=parent,
    ).show()
