"""Dialog for managing block exam subdecks."""

import uuid
from datetime import date, datetime
from typing import List, Optional

import aqt
from aqt.qt import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDateEdit,
    QMessageBox,
    Qt,
    QWidget,
)
from aqt.utils import showInfo, tooltip

from .. import LOGGER
from ..main.block_exam_subdecks import (
    get_existing_block_exam_subdecks,
    create_block_exam_subdeck,
    add_notes_to_block_exam_subdeck,
    validate_subdeck_name,
    validate_due_date,
)
from ..settings import config


class BlockExamSubdeckDialog(QDialog):
    """Main dialog for block exam subdeck management."""
    
    def __init__(self, ankihub_deck_id: uuid.UUID, note_ids: List[int], parent=None):
        super().__init__(parent)
        self.ankihub_deck_id = ankihub_deck_id
        self.note_ids = note_ids
        self.selected_subdeck_name: Optional[str] = None
        self.selected_subdeck_id: Optional[str] = None
        
        self.setWindowTitle("Add to Block Exam Subdeck")
        self.setModal(True)
        self.resize(450, 350)
        
        # Check if user has existing subdecks to determine entry point
        existing_subdecks = get_existing_block_exam_subdecks(ankihub_deck_id)
        if existing_subdecks:
            self._show_choose_subdeck_screen()
        else:
            self._show_create_subdeck_screen()
    
    def _show_choose_subdeck_screen(self):
        """Show screen for choosing existing subdeck or creating new one."""
        self._clear_layout()
        
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Choose Block Exam Subdeck")
        title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(title)
        
        # Filter input
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.textChanged.connect(self._filter_subdecks)
        self.filter_input.setPlaceholderText("Search subdecks...")
        filter_layout.addWidget(self.filter_input)
        layout.addLayout(filter_layout)
        
        # Subdeck list
        self.subdeck_list = QListWidget()
        self._populate_subdeck_list()
        self.subdeck_list.itemDoubleClicked.connect(self._on_subdeck_selected)
        layout.addWidget(self.subdeck_list)
        
        # Info label
        info_label = QLabel(f"Adding {len(self.note_ids)} note(s)")
        info_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(info_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        select_button = QPushButton("Select")
        select_button.clicked.connect(self._on_subdeck_selected)
        select_button.setDefault(True)
        button_layout.addWidget(select_button)
        
        create_button = QPushButton("Create New")
        create_button.clicked.connect(self._show_create_subdeck_screen)
        button_layout.addWidget(create_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
    
    def _show_create_subdeck_screen(self):
        """Show screen for creating new subdeck."""
        self._clear_layout()
        
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Create Block Exam Subdeck")
        title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(title)
        
        # Info label
        info_label = QLabel(f"Creating subdeck for {len(self.note_ids)} note(s)")
        info_label.setStyleSheet("color: gray; font-size: 11px; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        # Subdeck name input
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Subdeck Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter subdeck name")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)
        
        # Due date input
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("Due Date:"))
        self.date_input = QDateEdit()
        self.date_input.setDate(date.today())
        self.date_input.setCalendarPopup(True)
        self.date_input.setMinimumDate(date.today())
        date_layout.addWidget(self.date_input)
        layout.addLayout(date_layout)
        
        # Add some spacing
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        create_button = QPushButton("Create Subdeck")
        create_button.clicked.connect(self._on_create_subdeck)
        create_button.setDefault(True)
        button_layout.addWidget(create_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        
        # Focus on name input
        self.name_input.setFocus()
    
    def _show_add_notes_screen(self):
        """Show screen for adding notes to selected subdeck."""
        self._clear_layout()
        
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel(f"Add Notes to '{self.selected_subdeck_name}'")
        title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(title)
        
        # Pre-filled info
        info_label = QLabel(f"Adding {len(self.note_ids)} note(s) to subdeck")
        info_label.setStyleSheet("color: gray; font-size: 11px; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        # Due date input (pre-filled if exists)
        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("Due Date:"))
        self.date_input = QDateEdit()
        
        # Try to get existing due date
        existing_due_date = config.get_block_exam_subdeck_due_date(
            str(self.ankihub_deck_id), self.selected_subdeck_id
        )
        if existing_due_date:
            self.date_input.setDate(datetime.strptime(existing_due_date, "%Y-%m-%d").date())
        else:
            self.date_input.setDate(date.today())
        
        self.date_input.setCalendarPopup(True)
        self.date_input.setMinimumDate(date.today())
        date_layout.addWidget(self.date_input)
        layout.addLayout(date_layout)
        
        # Add some spacing
        layout.addStretch()
        
        # Buttons
        button_layout = QHBoxLayout()
        
        add_button = QPushButton("Add Notes")
        add_button.clicked.connect(self._on_add_notes)
        add_button.setDefault(True)
        button_layout.addWidget(add_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
    
    def _show_subdeck_conflict_screen(self, conflicting_name: str):
        """Show screen for handling subdeck name conflicts."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Subdeck Name Conflict")
        msg.setText(f"Subdeck name already exists with the name '{conflicting_name}'.")
        msg.setInformativeText("What would you like to do?")
        
        create_new_btn = msg.addButton("Create New", QMessageBox.ButtonRole.ActionRole)
        merge_btn = msg.addButton("Merge", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        
        msg.exec()
        clicked_button = msg.clickedButton()
        
        if clicked_button == create_new_btn:
            # Create with auto-generated name
            self._create_subdeck_with_auto_name(conflicting_name)
        elif clicked_button == merge_btn:
            # Merge into existing subdeck
            self.selected_subdeck_name = conflicting_name
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
            if subdeck:
                self.selected_subdeck_id = str(subdeck["id"])
                self._show_add_notes_screen()
            else:
                showInfo("Error: Could not find the existing subdeck.")
        # Cancel - stay on current screen (do nothing)
    
    def _populate_subdeck_list(self):
        """Populate the subdeck list widget."""
        self.subdeck_list.clear()
        existing_subdecks = get_existing_block_exam_subdecks(self.ankihub_deck_id)
        
        for subdeck_name, subdeck_id in existing_subdecks:
            item = QListWidgetItem(subdeck_name)
            item.setData(Qt.ItemDataRole.UserRole, subdeck_id)
            self.subdeck_list.addItem(item)
    
    def _filter_subdecks(self):
        """Filter subdeck list based on input."""
        filter_text = self.filter_input.text().lower()
        for i in range(self.subdeck_list.count()):
            item = self.subdeck_list.item(i)
            item.setHidden(filter_text not in item.text().lower())
    
    def _on_subdeck_selected(self):
        """Handle subdeck selection."""
        current_item = self.subdeck_list.currentItem()
        if not current_item:
            showInfo("Please select a subdeck first.")
            return
        
        self.selected_subdeck_name = current_item.text()
        self.selected_subdeck_id = current_item.data(Qt.ItemDataRole.UserRole)
        self._show_add_notes_screen()
    
    def _on_create_subdeck(self):
        """Handle subdeck creation."""
        name = self.name_input.text().strip()
        if not validate_subdeck_name(name):
            showInfo("Please enter a valid subdeck name.")
            return
        
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
            
        full_name = f"test::{name}"
        
        if aqt.mw.col.decks.by_name(full_name):
            self._show_subdeck_conflict_screen(name)
            return
        
        # Create subdeck
        try:
            actual_name, was_renamed = create_block_exam_subdeck(
                self.ankihub_deck_id, name, due_date
            )
            
            # Add notes to the new subdeck
            add_notes_to_block_exam_subdeck(
                self.ankihub_deck_id, actual_name, self.note_ids, due_date
            )
            
            # Show success message
            tooltip(f"{len(self.note_ids)} note(s) added to '{actual_name}'")
            self.accept()
            
        except Exception as e:
            LOGGER.error("Failed to create subdeck", error=str(e))
            showInfo(f"Failed to create subdeck: {e}")
    
    def _on_add_notes(self):
        """Handle adding notes to selected subdeck."""
        due_date = self.date_input.date().toString("yyyy-MM-dd")
        if not validate_due_date(due_date):
            showInfo("Due date must be in the future.")
            return
        
        try:
            add_notes_to_block_exam_subdeck(
                self.ankihub_deck_id, 
                self.selected_subdeck_name, 
                self.note_ids, 
                due_date
            )
            
            tooltip(f"{len(self.note_ids)} note(s) added to '{self.selected_subdeck_name}'")
            self.accept()
            
        except Exception as e:
            LOGGER.error("Failed to add notes to subdeck", error=str(e))
            showInfo(f"Failed to add notes: {e}")
    
    def _create_subdeck_with_auto_name(self, base_name: str):
        """Create subdeck with automatically generated name."""
        try:
            due_date = self.date_input.date().toString("yyyy-MM-dd")
            actual_name, _ = create_block_exam_subdeck(
                self.ankihub_deck_id, base_name, due_date
            )
            
            add_notes_to_block_exam_subdeck(
                self.ankihub_deck_id, actual_name, self.note_ids, due_date
            )
            
            tooltip(f"{len(self.note_ids)} note(s) added to '{actual_name}'")
            self.accept()
            
        except Exception as e:
            LOGGER.error("Failed to create subdeck with auto name", error=str(e))
            showInfo(f"Failed to create subdeck: {e}")
    
    def _clear_layout(self):
        """Clear the current layout."""
        if self.layout():
            QWidget().setLayout(self.layout())