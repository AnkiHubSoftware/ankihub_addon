"""Dialog summarizing the result of a bulk note suggestion submit, including the
interactive "Notes already in this deck" action that resubmits new-note suggestions
as change suggestions for the notes that already exist on AnkiHub."""

import uuid
from concurrent.futures import Future
from enum import Enum
from html import escape
from typing import Dict, List, Mapping, Optional, Set, Tuple

import aqt
from anki.notes import NoteId
from aqt.qt import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
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
from .utils import clear_layout, panel_line_color, show_error_dialog

SUPPORT_FORUM_URL = "https://community.ankihub.net/c/support"
MUTED_TEXT_COLOR = "#9aa0a6"

# (key, label, error substring). "other_errors" is the catch-all and is not listed here.
BULK_ERROR_CATEGORIES = [
    ("notes_without_changes", "Notes without changes", ANKIHUB_NO_CHANGE_ERROR),
    ("notes_dont_exist", "Notes that don't exist on AnkiHub", ANKIHUB_NOTE_DOES_NOT_EXIST_ERROR),
    ("empty_first_field", "Notes with the first field empty", ANKIHUB_EMPTY_FIRST_FIELD_ERROR),
]


def _summary_color(positive: bool) -> str:
    if positive:
        return "#66bb6a" if theme_manager.night_mode else "#2e7d32"
    return "#ef5350" if theme_manager.night_mode else "#c62828"


def _summary_link_color() -> str:
    return "#5b9bd5" if theme_manager.night_mode else "#1565c0"


def _updated_badge_colors() -> Tuple[str, str]:
    return ("#23492c", "#9be8ab") if theme_manager.night_mode else ("#d8f5dd", "#1b7a2f")


class _ActionState(Enum):
    """State of the 'Notes already in this deck' action box."""

    DEFAULT = "default"
    LOADING = "loading"  # resubmit in flight
    IGNORED = "ignored"  # user chose to ignore
    RESOLVED = "resolved"  # resubmit finished (fully or partially)
    FAILED = "failed"  # resubmit hit a hard error; user may retry and can close


class BulkSuggestionSummaryDialog(QDialog):
    """Summary shown after every bulk suggestion submit. Replaces the old plain-text
    `showText` summary: categorizes results and offers an interactive "Notes already
    in this deck" action that resubmits as change suggestions."""

    def __init__(
        self,
        result: BulkNoteSuggestionsResult,
        ah_did: uuid.UUID,
        auto_accept: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AnkiHub | Bulk Suggestion Summary")
        self.setMinimumWidth(500)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        self._errors_by_nid: Dict[NoteId, object] = dict(result.errors_by_nid)
        self._already_in_deck: Dict[NoteId, AlreadyInDeckConflict] = dict(result.already_in_deck_by_nid)
        self._change_submitted = result.change_note_suggestions_count
        self._new_submitted = result.new_note_suggestions_count
        self._ah_did = ah_did
        self._auto_accept = auto_accept
        self._action_state = _ActionState.DEFAULT
        self._updated_keys: Set[str] = set()  # "change_submitted" + category keys to badge

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        # Content (summary + action box) is rebuilt on each state change. The category
        # count is bounded (<= 5) and id lists are truncated, so the dialog stays a
        # sensible size without a scroll area — and the action box + Close are always
        # visible rather than scrolling off.
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        outer.addLayout(self._content_layout, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._close_button = QPushButton("Close")
        qconnect(self._close_button.clicked, self.accept)
        footer.addWidget(self._close_button)
        outer.addLayout(footer)

        if self._already_in_deck:
            LOGGER.info("bulk_error_category_shown", ah_did=str(self._ah_did), count=len(self._already_in_deck))

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

    def closeEvent(self, event) -> None:
        if self._can_close():
            super().closeEvent(event)
        else:
            event.ignore()

    def _categorize(self) -> Dict[str, List[NoteId]]:
        already = set(self._already_in_deck.keys())
        cats: Dict[str, List[NoteId]] = {key: [] for key, _, _ in BULK_ERROR_CATEGORIES}
        cats["other_errors"] = []
        for nid, errors in self._errors_by_nid.items():
            if nid in already:
                continue
            error_text = str(errors)
            for key, _, substring in BULK_ERROR_CATEGORIES:
                if substring in error_text:
                    cats[key].append(nid)
                    break
            else:
                cats["other_errors"].append(nid)
        return cats

    # --- rendering --------------------------------------------------------
    _NID_DISPLAY_CAP = 25

    @classmethod
    def _format_nids(cls, nids: List[NoteId]) -> str:
        shown = ", ".join(str(nid) for nid in nids[: cls._NID_DISPLAY_CAP])
        extra = len(nids) - cls._NID_DISPLAY_CAP
        if extra > 0:
            shown += f", … and {extra} more"
        return shown

    def _render(self) -> None:
        clear_layout(self._content_layout)
        self._content_layout.addWidget(self._build_summary_card())
        if self._already_in_deck:
            heading = QLabel("Action required")
            heading.setStyleSheet("font-weight:700;")
            self._content_layout.addWidget(heading)
            self._content_layout.addWidget(self._build_action_card())
        self._content_layout.addStretch(1)
        self._close_button.setEnabled(self._can_close())
        self.adjustSize()

    def _new_card(self, object_name: str) -> Tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName(object_name)
        border = panel_line_color()
        background = "#2b2b2b" if theme_manager.night_mode else "white"
        card.setStyleSheet(
            f"QFrame#{object_name}{{border:1px solid {border}; border-radius:8px; background:{background};}}"
        )
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(10)
        return card, vbox

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        color = panel_line_color()
        line.setStyleSheet(f"color:{color}; background:{color}; max-height:1px; border:none;")
        return line

    def _badge(self) -> QLabel:
        background, foreground = _updated_badge_colors()
        badge = QLabel("Updated")
        badge.setStyleSheet(f"background:{background}; color:{foreground}; border-radius:9px; padding:2px 9px;")
        return badge

    def _summary_line(self, number: int, label: str, color: Optional[str], badge: bool = False) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background:transparent;")
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if color:
            text = f'<span style="color:{color};"><b>{number}</b> {escape(label)}</span>'
        else:
            text = f"<b>{number}</b> {escape(label)}"
        line = QLabel(text)
        line.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(line)
        if badge:
            row.addWidget(self._badge())
        row.addStretch(1)
        return widget

    def _category_block(self, key: str, label: str, nids: List[NoteId], with_support: bool = False) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background:transparent;")
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel(f"({len(nids)}) {label}")
        title.setStyleSheet("font-weight:600;")
        title_row.addWidget(title)
        if key in self._updated_keys:
            title_row.addWidget(self._badge())
        title_row.addStretch(1)
        vbox.addLayout(title_row)

        ids = QLabel(self._format_nids(nids))
        ids.setWordWrap(True)
        vbox.addWidget(ids)

        if with_support:
            link = QLabel(
                f'Please <a href="{SUPPORT_FORUM_URL}" style="color:{_summary_link_color()};">contact support.</a>'
            )
            link.setTextFormat(Qt.TextFormat.RichText)
            qconnect(link.linkActivated, lambda *_: openLink(SUPPORT_FORUM_URL))
            vbox.addWidget(link)
        return widget

    def _build_summary_card(self) -> QFrame:
        card, vbox = self._new_card("summaryCard")
        vbox.addWidget(
            self._summary_line(
                self._change_submitted,
                "change note suggestion(s) submitted.",
                _summary_color(True),
                badge="change_submitted" in self._updated_keys,
            )
        )
        vbox.addWidget(self._summary_line(self._new_submitted, "new note suggestion(s) submitted.", None))
        vbox.addWidget(self._summary_line(len(self._errors_by_nid), "failed to submit.", _summary_color(False)))

        cats = self._categorize()
        for key, label, _ in BULK_ERROR_CATEGORIES:
            if cats[key]:
                vbox.addWidget(self._divider())
                vbox.addWidget(self._category_block(key, label, cats[key]))
        if cats["other_errors"]:
            vbox.addWidget(self._divider())
            vbox.addWidget(
                self._category_block("other_errors", "Other errors", cats["other_errors"], with_support=True)
            )
        return card

    def _build_action_card(self) -> QFrame:
        card, vbox = self._new_card("actionCard")
        nids = list(self._already_in_deck.keys())

        title = QLabel(f"({len(nids)}) Notes already in this deck")
        title.setStyleSheet("font-weight:600;")
        vbox.addWidget(title)

        ids = QLabel(self._format_nids(nids))
        ids.setWordWrap(True)
        vbox.addWidget(ids)

        description = QLabel(
            "These notes already exist in this deck on AnkiHub. Resubmit to push your "
            "edits through as change suggestions."
        )
        description.setWordWrap(True)
        vbox.addWidget(description)

        if self._action_state == _ActionState.IGNORED:
            ignored = QLabel("Ignored — no action taken.")
            ignored.setStyleSheet(f"color:{MUTED_TEXT_COLOR}; font-style:italic;")
            vbox.addWidget(ignored)
            return card

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 6, 0, 0)
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
            qconnect(resubmit.clicked, self._on_resubmit)
            button_row.addWidget(resubmit)
        button_row.addStretch(1)
        vbox.addLayout(button_row)
        return card

    # --- actions ----------------------------------------------------------
    def _on_ignore(self) -> None:
        self._action_state = _ActionState.IGNORED
        self._render()

    def _on_resubmit(self) -> None:
        LOGGER.info("bulk_resubmit_clicked", ah_did=str(self._ah_did), count=len(self._already_in_deck))
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

        self._already_in_deck = {}
        self._action_state = _ActionState.RESOLVED

        # Badge the error categories the re-failed notes landed in.
        for key, nids in self._categorize().items():
            if any(nid in failed for nid in nids):
                self._updated_keys.add(key)

        self._render()


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
