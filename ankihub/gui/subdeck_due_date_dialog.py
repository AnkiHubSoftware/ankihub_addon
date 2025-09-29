"""Dialog for handling expired block exam subdecks."""

from datetime import date, timedelta
from typing import Optional

import aqt
from anki.decks import DeckId
from aqt.qt import (
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.utils import showInfo, tooltip

from .. import LOGGER
from ..main.block_exam_subdecks import (
    check_block_exam_subdeck_due_dates,
    move_subdeck_to_main_deck,
    remove_block_exam_subdeck_config,
    set_subdeck_due_date,
)
from ..settings import BlockExamSubdeckConfig


class SubdeckDueDateDialog(QDialog):
    """Dialog shown when a block exam subdeck's due date is reached."""

    def __init__(self, subdeck_config: BlockExamSubdeckConfig, subdeck_name: str, parent=None):
        super().__init__(parent)
        self.subdeck_config = subdeck_config
        self.subdeck_name = subdeck_name

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("")  # Empty title as per screenshot
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
        self.set_new_date_button.clicked.connect(self._on_set_new_due_date)  # type: ignore[attr-defined]
        button_layout.addWidget(self.set_new_date_button)

        # Keep as is button (middle)
        self.keep_as_is_button = QPushButton("Keep it")
        self.keep_as_is_button.clicked.connect(self._on_keep_as_is)  # type: ignore[attr-defined]
        button_layout.addWidget(self.keep_as_is_button)

        # Move to main deck button (rightmost, primary action)
        self.move_to_main_button = QPushButton("Move to main deck")
        self.move_to_main_button.clicked.connect(self._on_move_to_main_deck)  # type: ignore[attr-defined]
        self.move_to_main_button.setDefault(True)
        button_layout.addWidget(self.move_to_main_button)
        self.adjustSize()

        main_layout.addLayout(button_layout)

    def _on_move_to_main_deck(self):
        """Handle moving subdeck to main deck."""
        try:
            success = move_subdeck_to_main_deck(self.subdeck_config)
            if success:
                tooltip(f"'{self.subdeck_name}' moved to main deck", parent=aqt.mw)
                self.accept()
                aqt.mw.deckBrowser.refresh()
            else:
                showInfo("Failed to move subdeck to main deck. Please try again.", parent=aqt.mw)
        except Exception as e:
            LOGGER.error("Error moving subdeck to main deck", error=str(e))
            showInfo(f"An error occurred: {e}", parent=aqt.mw)

    def _on_keep_as_is(self):
        """Handle keeping subdeck unchanged."""
        set_subdeck_due_date(self.subdeck_config, None)
        self.accept()

    def _on_set_new_due_date(self):
        """Handle setting a new due date."""
        self._show_date_picker()

    def _show_date_picker(self):
        """Show date picker dialog."""
        date_picker_dialog = DatePickerDialog(self.subdeck_name, self.subdeck_config, parent=aqt.mw)
        result = date_picker_dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self.accept()


class DatePickerDialog(QDialog):
    """Dialog for selecting a new due date."""

    def __init__(self, subdeck_name: str, subdeck_config: BlockExamSubdeckConfig, parent=None):
        super().__init__(parent)
        self.subdeck_name = subdeck_name
        self.subdeck_config = subdeck_config
        self.selected_date: Optional[str] = None

        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("")
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
        last_due_date = date.fromisoformat(self.subdeck_config.due_date)
        self.date_input.setDate(last_due_date)
        self.date_input.setCalendarPopup(True)
        self.date_input.setMinimumDate(date.today() + timedelta(days=1))
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
        cancel_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        button_layout.addWidget(cancel_button)

        # Confirm button
        confirm_button = QPushButton("Save")
        confirm_button.clicked.connect(self._on_confirm)  # type: ignore[attr-defined]
        confirm_button.setDefault(True)
        button_layout.addWidget(confirm_button)

        main_layout.addLayout(button_layout)

        # Focus on date input
        self.date_input.setFocus()

    def _on_confirm(self):
        """Handle confirming the selected date."""
        selected_date_str = self.date_input.date().toString("yyyy-MM-dd")

        self.selected_date = selected_date_str

        success = set_subdeck_due_date(self.subdeck_config, selected_date_str)
        if success:
            tooltip(f"Due date for <strong>{self.subdeck_name}</strong> updated successfully", parent=aqt.mw)
            self.accept()
        else:
            showInfo("Failed to update due date. Please try again.", parent=aqt.mw)


def handle_expired_subdeck(subdeck_config: BlockExamSubdeckConfig) -> None:
    """Handle an expired subdeck by showing the due date dialog.

    Args:
        subdeck_config: Configuration of the expired subdeck
    """
    from ..gui.subdeck_due_date_dialog import SubdeckDueDateDialog

    subdeck_id = DeckId(int(subdeck_config.subdeck_id))
    subdeck = aqt.mw.col.decks.get(subdeck_id, default=False)
    if not subdeck:
        LOGGER.warning("Expired subdeck not found, removing config", subdeck_id=subdeck_config.subdeck_id)
        remove_block_exam_subdeck_config(subdeck_config)
        return

    subdeck_name = subdeck["name"].split("::", maxsplit=1)[-1]  # Get name without parent deck prefix

    dialog = SubdeckDueDateDialog(subdeck_config, subdeck_name, parent=aqt.mw)
    dialog.exec()


def check_and_handle_block_exam_subdeck_due_dates() -> None:
    """Check for expired block exam subdecks and handle each one."""
    expired_subdecks = check_block_exam_subdeck_due_dates()
    for subdeck_config in expired_subdecks:
        handle_expired_subdeck(subdeck_config)
