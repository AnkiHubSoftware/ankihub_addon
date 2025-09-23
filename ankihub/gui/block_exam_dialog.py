"""Dialog for managing block exam subdecks."""

import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

import aqt
from anki.notes import NoteId
from aqt.qt import (
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    Qt,
    QVBoxLayout,
)
from aqt.utils import showInfo, tooltip

from .. import LOGGER
from ..main.block_exam_subdecks import (
    add_notes_to_block_exam_subdeck,
    create_block_exam_subdeck,
    validate_due_date,
)
from ..settings import config
from .utils import clear_layout


class BlockExamSubdeckDialog(QDialog):
    """Main dialog for block exam subdeck management."""

    def __init__(self, ankihub_deck_id: uuid.UUID, note_ids: List[NoteId], parent=None):
        super().__init__(parent)
        self.ankihub_deck_id = ankihub_deck_id
        self.note_ids = note_ids
        self.selected_subdeck_name: Optional[str] = None
        self.selected_subdeck_id: Optional[str] = None

        self.setModal(True)
        self.resize(440, 340)

        # Check if user has existing subdecks to determine entry point
        deck_config = config.deck_config(ankihub_deck_id)
        has_subdecks = False

        if deck_config:
            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            if anki_deck_name:
                # Check if there are any subdecks under the parent deck
                has_subdecks = len(list(aqt.mw.col.decks.children(deck_config.anki_id))) > 0

        layout = QVBoxLayout()
        self.setLayout(layout)

        if has_subdecks:
            self._show_choose_subdeck_screen()
        else:
            self._show_create_subdeck_screen()

    def _show_choose_subdeck_screen(self):
        """Show screen for choosing existing subdeck or creating new one."""
        self._clear_layout()

        layout = self.layout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        self.setWindowTitle("Choose Subdeck")

        # Filter input
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.textChanged.connect(self._filter_subdecks)  # type: ignore[attr-defined]
        self.filter_input.setPlaceholderText("Search subdecks...")
        filter_layout.addWidget(self.filter_input)
        layout.addLayout(filter_layout)

        # Subdeck list
        self.subdeck_list = QListWidget()
        self._populate_subdeck_list()
        self.subdeck_list.itemDoubleClicked.connect(self._on_subdeck_selected)  # type: ignore[attr-defined]
        layout.addWidget(self.subdeck_list)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        button_layout.addWidget(cancel_button)

        create_button = QPushButton("Create new subdeck")
        create_button.clicked.connect(self._show_create_subdeck_screen)  # type: ignore[attr-defined]
        button_layout.addWidget(create_button)

        select_button = QPushButton("Choose")
        select_button.clicked.connect(self._on_subdeck_selected)  # type: ignore[attr-defined]
        select_button.setDefault(True)
        button_layout.addWidget(select_button)

        layout.addLayout(button_layout)

    def _show_create_subdeck_screen(self):
        """Show screen for creating new subdeck."""
        self._clear_layout()

        layout = self.layout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)  # default spacing between elements is 12

        self.setWindowTitle("")

        # Title
        title = QLabel("Create a subdeck")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        layout.addWidget(title)

        # Info label
        info_label = QLabel(
            "Selected notes will be moved into this subdeck. Once the due date is reached, we'll ask you if you'd like "
            "to return all notes back into the parent deck."
        )
        info_font = info_label.font()
        info_label.setFont(info_font)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        layout.addSpacing(8)  # 12 (default) + 8 = 20 px after info label

        # Subdeck name input
        name_layout = QVBoxLayout()
        name_label = QLabel("Subdeck Name:")
        name_label_font = name_label.font()
        name_label_font.setBold(True)
        name_label.setFont(name_label_font)
        name_layout.addWidget(name_label)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Cardio Block Exam")
        self.name_input.textChanged.connect(self._update_create_button_state)  # type: ignore[attr-defined]
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)
        layout.addSpacing(8)

        # Due date input
        date_layout = QVBoxLayout()
        date_label = QLabel("Due date:")
        date_label_font = date_label.font()
        date_label_font.setBold(True)
        date_label.setFont(date_label_font)
        date_layout.addWidget(date_label)
        self.date_input = QDateEdit()
        tomorrow = date.today() + timedelta(days=1)
        self.date_input.setDate(tomorrow)
        self.date_input.setCalendarPopup(True)
        self.date_input.setMinimumDate(tomorrow)
        date_layout.addWidget(self.date_input)
        layout.addLayout(date_layout)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        button_layout.addWidget(cancel_button)

        create_button = QPushButton("Create subdeck")
        create_button.clicked.connect(self._on_create_subdeck)  # type: ignore[attr-defined]
        create_button.setDefault(True)
        create_button.setEnabled(False)  # Initially disabled
        self.create_button = create_button  # Store reference for enabling/disabling
        button_layout.addWidget(create_button)

        layout.addLayout(button_layout)

        # Focus on name input
        self.name_input.setFocus()

    def _show_add_notes_screen(self):
        """Show screen for adding notes to selected subdeck."""
        self._clear_layout()

        self.setWindowTitle("")

        layout = self.layout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(12)

        # Title
        title = QLabel("Add notes to the subdeck")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        layout.addWidget(title)

        # Info label
        info_label = QLabel(
            "Selected notes will be moved into this subdeck. Once the due date is reached, we'll ask you if you'd like "
            "to return all notes back into the parent deck."
        )
        info_font = info_label.font()
        info_label.setFont(info_font)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        layout.addSpacing(8)  # 12 (default) + 8 = 20 px after info label

        # Subdeck name input (editable)
        name_layout = QVBoxLayout()
        name_label = QLabel("Subdeck Name:")
        name_label_font = name_label.font()
        name_label_font.setBold(True)
        name_label.setFont(name_label_font)
        name_layout.addWidget(name_label)
        self.name_input = QLineEdit()
        self.name_input.setText(self.selected_subdeck_name)
        self.name_input.setPlaceholderText("Enter subdeck name")
        self.name_input.textChanged.connect(self._update_add_notes_button_state)  # type: ignore[attr-defined]
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)
        layout.addSpacing(8)

        # Due date input (pre-filled if exists)
        date_layout = QVBoxLayout()
        date_label = QLabel("Due date:")
        date_label_font = date_label.font()
        date_label_font.setBold(True)
        date_label.setFont(date_label_font)
        date_layout.addWidget(date_label)
        self.date_input = QDateEdit()

        tomorrow = date.today() + timedelta(days=1)

        # Try to get existing due date
        existing_due_date = config.get_block_exam_subdeck_due_date(str(self.ankihub_deck_id), self.selected_subdeck_id)
        if existing_due_date:
            self.date_input.setDate(datetime.strptime(existing_due_date, "%Y-%m-%d").date())
        else:
            self.date_input.setDate(tomorrow)

        self.date_input.setCalendarPopup(True)
        self.date_input.setMinimumDate(tomorrow)
        date_layout.addWidget(self.date_input)
        layout.addLayout(date_layout)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        button_layout.addWidget(cancel_button)

        add_button = QPushButton("Add notes")
        add_button.clicked.connect(self._on_add_notes)  # type: ignore[attr-defined]
        add_button.setDefault(True)
        # Enable initially if there's already a name, disable if empty
        add_button.setEnabled(bool(self.selected_subdeck_name and self.selected_subdeck_name.strip()))
        self.add_notes_button = add_button  # Store reference for enabling/disabling
        button_layout.addWidget(add_button)

        layout.addLayout(button_layout)

    def _show_subdeck_conflict_screen(self, conflicting_name: str):
        """Show screen for handling subdeck name conflicts."""
        # Store the due date from the current screen before clearing layout
        if hasattr(self, "date_input") and self.date_input:
            self.stored_due_date = self.date_input.date().toString("yyyy-MM-dd")
        else:
            self.stored_due_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

        self._clear_layout()

        self.setWindowTitle("")

        layout = self.layout()
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(0)

        # Title
        title = QLabel("Subdeck name already exists")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        layout.addWidget(title)
        layout.addSpacing(12)

        # Main message
        message_label = QLabel(f"A subdeck already exists with the name '{conflicting_name}'.")
        message_font = message_label.font()
        message_label.setFont(message_font)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)
        layout.addSpacing(1)

        # Info label
        info_label = QLabel(
            "You can either create a new one called "
            f"'{conflicting_name} (1)' or merge these notes into the existing subdeck."
        )
        info_font = info_label.font()
        info_label.setFont(info_font)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.setSpacing(12)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)  # type: ignore[attr-defined]
        button_layout.addWidget(cancel_button)

        create_new_button = QPushButton("Create new")
        create_new_button.clicked.connect(lambda: self._handle_conflict_create_new(conflicting_name))  # type: ignore[attr-defined]
        button_layout.addWidget(create_new_button)

        merge_button = QPushButton("Merge")
        merge_button.clicked.connect(lambda: self._handle_conflict_merge(conflicting_name))  # type: ignore[attr-defined]
        merge_button.setDefault(True)
        button_layout.addWidget(merge_button)

        layout.addLayout(button_layout)

    def _populate_subdeck_list(self):
        """Populate the subdeck list widget."""
        self.subdeck_list.clear()

        # Get the parent deck configuration
        deck_config = config.deck_config(self.ankihub_deck_id)
        if not deck_config:
            return

        anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
        if not anki_deck_name:
            return

        # Get ALL subdecks under the parent deck (including nested ones)
        all_subdecks = []
        
        # Get all deck names and IDs in the collection
        for deck_dict in aqt.mw.col.decks.all_names_and_ids():
            deck_name = deck_dict.name
            deck_id = deck_dict.id
            
            # Check if this deck is a subdeck of our parent deck
            if deck_name.startswith(f"{anki_deck_name}::") and deck_name != anki_deck_name:
                # Extract the subdeck path (everything after the parent deck name)
                subdeck_path = deck_name[len(anki_deck_name) + 2:]  # +2 for "::"
                all_subdecks.append((subdeck_path, str(deck_id)))

        # Sort subdecks alphabetically by name
        all_subdecks.sort(key=lambda x: x[0].lower())

        # Add all subdecks to the list
        for subdeck_name, subdeck_id in all_subdecks:
            item = QListWidgetItem(subdeck_name)
            item.setData(Qt.ItemDataRole.UserRole, subdeck_id)

            # Mark block exam subdecks differently (optional visual indication)
            if config.get_block_exam_subdeck_due_date(str(self.ankihub_deck_id), subdeck_id):
                # This subdeck is already configured as a block exam subdeck
                item.setToolTip("Block exam subdeck")

            self.subdeck_list.addItem(item)

        # If no subdecks found, show a message
        if not all_subdecks:
            no_subdecks_item = QListWidgetItem("No subdecks found")
            no_subdecks_item.setFlags(Qt.ItemFlag.NoItemFlags)
            no_subdecks_item.setData(Qt.ItemDataRole.UserRole, None)
            self.subdeck_list.addItem(no_subdecks_item)

    def _filter_subdecks(self):
        """Filter subdeck list based on input."""
        filter_text = self.filter_input.text().lower()
        for i in range(self.subdeck_list.count()):
            item = self.subdeck_list.item(i)
            item.setHidden(filter_text not in item.text().lower())

    def _update_create_button_state(self):
        """Enable/disable the Create subdeck button based on name input."""
        if hasattr(self, "create_button"):
            self.create_button.setEnabled(bool(self.name_input.text().strip()))

    def _update_add_notes_button_state(self):
        """Enable/disable the Add notes button based on name input."""
        if hasattr(self, "add_notes_button"):
            self.add_notes_button.setEnabled(bool(self.name_input.text().strip()))

    def _on_subdeck_selected(self):
        """Handle subdeck selection."""
        current_item = self.subdeck_list.currentItem()
        if not current_item:
            showInfo("Please select a subdeck first.")
            return

        # Check if this is a valid subdeck (not the "No subdecks found" placeholder)
        subdeck_id = current_item.data(Qt.ItemDataRole.UserRole)
        if subdeck_id is None:
            showInfo("Please select a valid subdeck.")
            return

        self.selected_subdeck_name = current_item.text()
        self.selected_subdeck_id = subdeck_id
        self._show_add_notes_screen()

    def _on_create_subdeck(self):
        """Handle subdeck creation."""
        name = self.name_input.text().strip()

        due_date = self.date_input.date().toString("yyyy-MM-dd")
        if not validate_due_date(due_date):
            showInfo("Due date must be in the future.")
            return

        # Check if subdeck already exists
        deck_config = config.deck_config(self.ankihub_deck_id)
        if not deck_config:
            showInfo("Error: Deck configuration not found.")
            return

        anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
        if not anki_deck_name:
            showInfo("Error: Parent deck not found.")
            return

        full_name = f"{anki_deck_name}::{name}"

        if aqt.mw.col.decks.by_name(full_name):
            self._show_subdeck_conflict_screen(name)
            return

        # Create subdeck
        try:
            actual_name, _ = create_block_exam_subdeck(self.ankihub_deck_id, name, due_date)

            # Add notes to the new subdeck
            add_notes_to_block_exam_subdeck(self.ankihub_deck_id, actual_name, self.note_ids, due_date)

            # Show success message
            tooltip(f"{len(self.note_ids)} note(s) added to '{actual_name}'")
            self.accept()

        except Exception as e:
            LOGGER.error("Failed to create subdeck", error=str(e))
            showInfo(f"Failed to create subdeck: {e}")

    def _on_add_notes(self):
        """Handle adding notes to selected subdeck."""
        new_name = self.name_input.text().strip()

        due_date = self.date_input.date().toString("yyyy-MM-dd")
        if not validate_due_date(due_date):
            showInfo("Due date must be in the future.")
            return

        try:
            # Check if subdeck name needs to be updated
            if new_name != self.selected_subdeck_name:
                # Check if new name conflicts with existing subdeck
                deck_config = config.deck_config(self.ankihub_deck_id)
                if deck_config:
                    anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
                    if anki_deck_name:
                        # Handle full subdeck path for multi-level subdecks
                        new_full_name = f"{anki_deck_name}::{new_name}"
                        if aqt.mw.col.decks.by_name(new_full_name):
                            showInfo(
                                f"A subdeck with name '{new_name}' already exists. Please choose a different name."
                            )
                            return

                self._rename_subdeck(self.selected_subdeck_name, new_name)
                self.selected_subdeck_name = new_name

            add_notes_to_block_exam_subdeck(self.ankihub_deck_id, self.selected_subdeck_name, self.note_ids, due_date)

            tooltip(f"{len(self.note_ids)} note(s) added to '{self.selected_subdeck_name}'")
            self.accept()

        except Exception as e:
            LOGGER.error("Failed to add notes to subdeck", error=str(e))
            showInfo(f"Failed to add notes: {e}")

    def _rename_subdeck(self, old_subdeck_path: str, new_subdeck_path: str):
        """Rename an existing subdeck."""
        deck_config = config.deck_config(self.ankihub_deck_id)
        if not deck_config:
            raise ValueError("Deck configuration not found")

        anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
        if not anki_deck_name:
            raise ValueError("Parent deck not found")

        old_full_name = f"{anki_deck_name}::{old_subdeck_path}"
        new_full_name = f"{anki_deck_name}::{new_subdeck_path}"

        # Get the subdeck to rename
        subdeck = aqt.mw.col.decks.by_name(old_full_name)
        if not subdeck:
            raise ValueError(f"Subdeck '{old_subdeck_path}' not found")

        # Rename the subdeck
        subdeck["name"] = new_full_name
        aqt.mw.col.decks.save(subdeck)

        LOGGER.info("Renamed subdeck", old_name=old_subdeck_path, new_name=new_subdeck_path)

    def _handle_conflict_create_new(self, conflicting_name: str):
        """Handle creating a new subdeck with auto-generated name."""
        try:
            # Use stored due date from the original screen
            due_date = getattr(self, "stored_due_date", (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"))

            actual_name, _ = create_block_exam_subdeck(self.ankihub_deck_id, conflicting_name, due_date)

            add_notes_to_block_exam_subdeck(self.ankihub_deck_id, actual_name, self.note_ids, due_date)

            tooltip(f"{len(self.note_ids)} note(s) added to '{actual_name}'")
            self.accept()

        except Exception as e:
            LOGGER.error("Failed to create subdeck with auto name", error=str(e))
            showInfo(f"Failed to create subdeck: {e}")

    def _handle_conflict_merge(self, conflicting_name: str):
        """Handle merging into existing subdeck directly."""
        try:
            # Find subdeck ID for the existing subdeck
            deck_config = config.deck_config(self.ankihub_deck_id)
            if not deck_config:
                showInfo("Error: Deck configuration not found.")
                return

            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            if not anki_deck_name:
                showInfo("Error: Parent deck not found.")
                return

            full_name = f"{anki_deck_name}::{conflicting_name}"
            subdeck = aqt.mw.col.decks.by_name(full_name)
            if not subdeck:
                showInfo("Error: Could not find the existing subdeck.")
                return

            # Use stored due date from the original screen
            due_date = getattr(self, "stored_due_date", (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"))

            # Add notes to the existing subdeck
            add_notes_to_block_exam_subdeck(self.ankihub_deck_id, conflicting_name, self.note_ids, due_date)

            # Show success message and close
            tooltip(f"{len(self.note_ids)} note(s) added to '{conflicting_name}'")
            self.accept()

        except Exception as e:
            LOGGER.error("Failed to add notes to subdeck", error=str(e))
            showInfo(f"Failed to add notes: {e}")

    def _clear_layout(self):
        """Clear the current layout."""
        old_layout = self.layout()
        if old_layout:
            clear_layout(old_layout)
