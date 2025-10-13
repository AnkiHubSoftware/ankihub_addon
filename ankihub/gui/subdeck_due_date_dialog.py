"""Dialog for handling expired block exam subdecks."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import aqt
from anki.decks import DeckId
from aqt import qconnect
from aqt.qt import QDateEdit, QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QVBoxLayout
from aqt.utils import tooltip

from .. import LOGGER
from ..main.block_exam_subdecks import (
    check_block_exam_subdeck_due_dates,
    get_subdeck_name_without_parent,
    move_subdeck_to_main_deck,
    remove_block_exam_subdeck_config,
    set_subdeck_due_date,
)
from ..settings import BlockExamSubdeckConfig


@dataclass
class _SubdeckDueDateDialogState:
    """State for managing sequential SubdeckDueDate dialogs."""

    queue: list[BlockExamSubdeckConfig] = field(default_factory=list)


_subdeck_due_date_dialog_state = _SubdeckDueDateDialogState()


class SubdeckDueDateDialog(QDialog):
    """Dialog shown when a block exam subdeck's due date is reached."""

    def __init__(self, subdeck_config: BlockExamSubdeckConfig, subdeck_name: str, parent=None):
        super().__init__(parent)
        self.subdeck_config = subdeck_config
        self.subdeck_name = subdeck_name

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("AnkiHub | Subdecks")
        self.setMinimumWidth(400)
        self.resize(440, 300)

        self._setup_ui()

    def _setup_ui(self):
        """Set up the dialog UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 28, 28, 28)
        main_layout.setSpacing(12)

        # Title
        title_label = QLabel("Subdeck due date reached")
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # Main message
        message_text = (
            f"The due date you set for <strong>{self.subdeck_name}</strong> has arrived. "
            "Please choose what you'd like to do next:"
        )
        message_label = QLabel(message_text)
        message_label.setWordWrap(True)
        main_layout.addWidget(message_label)

        # Options list
        options_text = (
            "<strong>• Move to main deck:</strong> Delete the subdeck and move all notes back into the main deck.<br>"
            "<strong>• Keep as is:</strong> Leave the subdeck and notes unchanged.<br>"
            "<strong>• Set new due date:</strong> Pick a new date to be reminded later."
        )
        options_label = QLabel(options_text)
        options_label.setWordWrap(True)
        main_layout.addWidget(options_label)

        # Add stretch to push buttons to bottom
        main_layout.addStretch()

        # Buttons layout
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        # Set new due date button (leftmost)
        self.set_new_date_button = QPushButton("Set new due date")
        qconnect(self.set_new_date_button.clicked, self._on_set_new_due_date)
        button_layout.addWidget(self.set_new_date_button)

        # Keep as is button (middle)
        self.keep_as_is_button = QPushButton("Keep it")
        qconnect(self.keep_as_is_button.clicked, self._on_keep_as_is)
        button_layout.addWidget(self.keep_as_is_button)

        # Move to main deck button (rightmost, primary action)
        self.move_to_main_button = QPushButton("Move to main deck")
        qconnect(self.move_to_main_button.clicked, self._on_move_to_main_deck)
        self.move_to_main_button.setDefault(True)
        button_layout.addWidget(self.move_to_main_button)

        main_layout.addLayout(button_layout)
        self.adjustSize()

    def _on_move_to_main_deck(self):
        """Handle moving subdeck to main deck."""
        move_subdeck_to_main_deck(self.subdeck_config.subdeck_id)
        tooltip(f"'{self.subdeck_name}' moved to main deck", parent=aqt.mw)
        self.accept()
        aqt.mw.deckBrowser.refresh()

    def _on_keep_as_is(self):
        """Handle keeping subdeck unchanged."""
        remove_block_exam_subdeck_config(self.subdeck_config)
        self.accept()

    def _on_set_new_due_date(self):
        """Handle setting a new due date."""
        self._show_date_picker()

    def _show_date_picker(self):
        """Show date picker dialog."""
        date_picker_dialog = DatePickerDialog(
            self.subdeck_name,
            self.subdeck_config.subdeck_id,
            self.subdeck_config.due_date,
            parent=self,
        )
        qconnect(date_picker_dialog.accepted, self.accept)
        date_picker_dialog.show()


class DatePickerDialog(QDialog):
    """Dialog for selecting a new due date."""

    def __init__(
        self,
        subdeck_name: str,
        subdeck_id: DeckId,
        initial_due_date: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.subdeck_name = subdeck_name
        self.subdeck_id = subdeck_id
        self.initial_due_date = initial_due_date
        self.selected_date: Optional[str] = None

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("AnkiHub | Subdecks")
        self.setMinimumWidth(221)
        self.resize(400, 221)

        self._setup_ui()

    def _setup_ui(self):
        """Set up the date picker dialog UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 28, 28, 28)
        main_layout.setSpacing(12)

        # Title
        title_label = QLabel("Reschedule due date")
        title_font = title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # Message
        message_text = f"Pick a new due date for <strong>{self.subdeck_name}</strong>."
        message_label = QLabel(message_text)
        message_label.setWordWrap(True)
        main_layout.addWidget(message_label)
        main_layout.addSpacing(8)

        # Date picker
        date_layout = QVBoxLayout()
        date_label = QLabel("Due date:")
        date_label_font = date_label.font()
        date_label_font.setBold(True)
        date_label.setFont(date_label_font)
        date_layout.addWidget(date_label)

        self.date_input = QDateEdit()
        last_due_date = (
            date.fromisoformat(self.initial_due_date) if self.initial_due_date else date.today() + timedelta(days=1)
        )
        self.date_input.setDate(last_due_date)
        self.date_input.setMinimumDate(date.today())
        self.date_input.setCalendarPopup(True)
        date_layout.addWidget(self.date_input)
        main_layout.addLayout(date_layout)

        # Add stretch to push buttons to bottom
        main_layout.addStretch()

        # Buttons layout
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        button_layout.addStretch()

        # Cancel button
        cancel_button = QPushButton("Cancel")
        qconnect(cancel_button.clicked, self.reject)
        button_layout.addWidget(cancel_button)

        # Confirm button
        confirm_button = QPushButton("Save")
        qconnect(confirm_button.clicked, self._on_confirm)
        confirm_button.setDefault(True)
        button_layout.addWidget(confirm_button)

        main_layout.addLayout(button_layout)

        # Focus on date input
        self.date_input.setFocus()

    def _on_confirm(self):
        """Handle confirming the selected date."""
        selected_date_str = self.date_input.date().toString("yyyy-MM-dd")

        self.selected_date = selected_date_str

        set_subdeck_due_date(self.subdeck_id, selected_date_str)

        tooltip(f"Due date for <strong>{self.subdeck_name}</strong> updated successfully", parent=aqt.mw)
        self.accept()


def handle_expired_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    """Handle an expired subdeck by showing the due date dialog.

    Args:
        subdeck_config: Configuration of the expired subdeck
    """
    subdeck_id = subdeck_config.subdeck_id
    subdeck = aqt.mw.col.decks.get(subdeck_id, default=False)

    # Validate subdeck exists and is actually a subdeck
    if not subdeck or "::" not in subdeck["name"]:
        LOGGER.warning(
            "Removing block exam subdeck config for missing or invalid subdeck",
            subdeck_id=subdeck_config.subdeck_id,
            reason="not found" if not subdeck else "not a subdeck",
        )
        remove_block_exam_subdeck_config(subdeck_config)
        _show_next_expired_subdeck_dialog()
        return

    subdeck_name = get_subdeck_name_without_parent(subdeck_id)
    dialog = SubdeckDueDateDialog(subdeck_config, subdeck_name, parent=aqt.mw)
    qconnect(dialog.finished, _show_next_expired_subdeck_dialog)
    dialog.show()


def _show_next_expired_subdeck_dialog() -> None:
    """Show the next expired subdeck dialog from the queue."""
    if not _subdeck_due_date_dialog_state.queue:
        return

    next_subdeck = _subdeck_due_date_dialog_state.queue.pop(0)
    handle_expired_subdeck(next_subdeck)


def check_and_handle_block_exam_subdeck_due_dates() -> None:
    """Check for expired block exam subdecks and handle each one."""
    expired_subdecks = check_block_exam_subdeck_due_dates()
    if not expired_subdecks:
        return

    _subdeck_due_date_dialog_state.queue = expired_subdecks
    _show_next_expired_subdeck_dialog()
