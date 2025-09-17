"""Tests for BlockExamSubdeckDialog."""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, List, Optional
from unittest.mock import Mock, patch

import pytest
from aqt import dialogs
from aqt.qt import Qt, QDialog, QListWidget, QLineEdit, QPushButton, QDateEdit, QMessageBox
from pytest_anki import AnkiSession
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot

if TYPE_CHECKING:
    from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
    from ankihub.main.block_exam_subdecks import BlockExamSubdeck

    # Prevent circular imports
    BlockExamSubdeckDialog = "BlockExamSubdeckDialog"
    BlockExamSubdeck = "BlockExamSubdeck"


class TestBlockExamSubdeckDialog:
    """Integration tests for BlockExamSubdeckDialog."""

    def setup_method(self):
        """Set up test fixtures."""
        self.note_ids = [1, 2, 3]

    def test_dialog_creation_and_initial_screen(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test dialog creates successfully and shows initial screen."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            # Mock dependencies
            mock_get_existing = mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Check dialog was created and configured properly
            assert dialog.note_ids == self.note_ids
            assert dialog.parent() is None
            
            # Check initial screen is shown
            assert hasattr(dialog, "subdeck_list")
            assert hasattr(dialog, "create_button")
            assert hasattr(dialog, "add_button")

            mock_get_existing.assert_called_once()

    @pytest.mark.parametrize(
        "has_existing_subdecks",
        [True, False],
    )
    def test_choose_subdeck_screen_display(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
        has_existing_subdecks: bool,
    ):
        """Test choose subdeck screen displays correctly with and without existing subdecks."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        existing_subdecks = []
        if has_existing_subdecks:
            existing_subdecks = [
                BlockExamSubdeck(
                    subdeck_id="test-id-1",
                    name="Exam 1",
                    due_date=date.today() + timedelta(days=7),
                ),
                BlockExamSubdeck(
                    subdeck_id="test-id-2", 
                    name="Exam 2",
                    due_date=date.today() + timedelta(days=14),
                ),
            ]

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=existing_subdecks,
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Check subdeck list is populated correctly
            if has_existing_subdecks:
                assert dialog.subdeck_list.count() == 2
                assert "Exam 1" in dialog.subdeck_list.item(0).text()
                assert "Exam 2" in dialog.subdeck_list.item(1).text()
                
                # Check buttons are enabled appropriately
                assert dialog.add_button.isEnabled() is False  # No selection yet
            else:
                assert dialog.subdeck_list.count() == 0
                assert dialog.add_button.isEnabled() is False

            # Test create button is always enabled
            assert dialog.create_button.isEnabled() is True

    def test_subdeck_selection_enables_add_button(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test selecting a subdeck enables the add button."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        existing_subdecks = [
            BlockExamSubdeck(
                subdeck_id="test-id-1",
                name="Exam 1",
                due_date=date.today() + timedelta(days=7),
            ),
        ]

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=existing_subdecks,
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Initially add button should be disabled
            assert dialog.add_button.isEnabled() is False

            # Select first item
            dialog.subdeck_list.setCurrentRow(0)
            qtbot.wait(100)

            # Now add button should be enabled
            assert dialog.add_button.isEnabled() is True

    def test_create_button_shows_create_screen(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test clicking create button shows create subdeck screen."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Click create button
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should now be on create screen
            assert hasattr(dialog, "name_input")
            assert hasattr(dialog, "date_input")
            assert hasattr(dialog, "finish_button")
            assert hasattr(dialog, "back_button")

    def test_create_screen_validation(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test create screen validates input and enables finish button appropriately."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to create screen
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Initially finish button should be disabled (no name entered)
            assert dialog.finish_button.isEnabled() is False

            # Enter a valid name
            dialog.name_input.setText("Valid Exam Name")
            qtbot.wait(100)

            # Should still be disabled (default date is today, need future date)
            assert dialog.finish_button.isEnabled() is False

            # Set future date
            future_date = date.today() + timedelta(days=7)
            dialog.date_input.setDate(future_date)
            qtbot.wait(100)

            # Now finish button should be enabled
            assert dialog.finish_button.isEnabled() is True

            # Test invalid name disables button
            dialog.name_input.setText("Invalid/Name")  # Contains invalid character
            qtbot.wait(100)
            assert dialog.finish_button.isEnabled() is False

            # Fix name but set past date
            dialog.name_input.setText("Valid Name")
            dialog.date_input.setDate(date.today() - timedelta(days=1))
            qtbot.wait(100)
            assert dialog.finish_button.isEnabled() is False

    def test_back_button_returns_to_choose_screen(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test back button returns from create screen to choose screen."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to create screen
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Verify we're on create screen
            assert hasattr(dialog, "name_input")

            # Click back button
            qtbot.mouseClick(dialog.back_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should be back on choose screen
            assert hasattr(dialog, "subdeck_list")
            assert hasattr(dialog, "create_button")

    def test_successful_subdeck_creation(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test successful subdeck creation workflow."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        with anki_session_with_addon_data.profile_loaded():
            mock_get_existing = mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )
            mock_create = mocker.patch(
                "ankihub.main.block_exam_subdecks.create_block_exam_subdeck",
                return_value=BlockExamSubdeck(
                    subdeck_id="new-id",
                    name="New Exam",
                    due_date=date.today() + timedelta(days=7),
                ),
            )
            mock_add_notes = mocker.patch(
                "ankihub.main.block_exam_subdecks.add_notes_to_block_exam_subdeck"
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to create screen
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Fill in valid data
            dialog.name_input.setText("New Exam")
            future_date = date.today() + timedelta(days=7)
            dialog.date_input.setDate(future_date)
            qtbot.wait(100)

            # Click finish button
            qtbot.mouseClick(dialog.finish_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Verify subdeck was created and notes added
            mock_create.assert_called_once_with("New Exam", future_date)
            mock_add_notes.assert_called_once_with("new-id", self.note_ids)

            # Dialog should be closed
            qtbot.wait_until(lambda: not dialog.isVisible(), timeout=1000)

    def test_subdeck_creation_conflict_handling(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test handling of subdeck name conflicts during creation."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )
            
            # Mock create function to raise conflict error
            mock_create = mocker.patch(
                "ankihub.main.block_exam_subdecks.create_block_exam_subdeck",
                side_effect=ValueError("Subdeck 'Exam 1' already exists"),
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to create screen and fill valid data
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)
            
            dialog.name_input.setText("Exam 1")
            future_date = date.today() + timedelta(days=7)
            dialog.date_input.setDate(future_date)
            qtbot.wait(100)

            # Mock the conflict resolution dialog
            mock_conflict_screen = mocker.patch.object(dialog, "_show_subdeck_conflict_screen")

            # Click finish button
            qtbot.mouseClick(dialog.finish_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should show conflict screen
            mock_conflict_screen.assert_called_once_with("Exam 1", future_date)

    def test_add_to_existing_subdeck_workflow(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test adding notes to existing subdeck workflow."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        existing_subdecks = [
            BlockExamSubdeck(
                subdeck_id="test-id-1",
                name="Existing Exam",
                due_date=date.today() + timedelta(days=7),
            ),
        ]

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=existing_subdecks,
            )
            mock_add_notes = mocker.patch(
                "ankihub.main.block_exam_subdecks.add_notes_to_block_exam_subdeck"
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Select existing subdeck
            dialog.subdeck_list.setCurrentRow(0)
            qtbot.wait(100)

            # Click add button
            qtbot.mouseClick(dialog.add_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should be on add notes screen
            assert hasattr(dialog, "confirm_button")
            assert hasattr(dialog, "back_button")

            # Click confirm button
            qtbot.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Verify notes were added to subdeck
            mock_add_notes.assert_called_once_with("test-id-1", self.note_ids)

            # Dialog should be closed
            qtbot.wait_until(lambda: not dialog.isVisible(), timeout=1000)

    def test_conflict_screen_merge_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test conflict screen merge option workflow."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        existing_subdecks = [
            BlockExamSubdeck(
                subdeck_id="existing-id",
                name="Existing Exam",
                due_date=date.today() + timedelta(days=5),
            ),
        ]

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=existing_subdecks,
            )
            mock_add_notes = mocker.patch(
                "ankihub.main.block_exam_subdecks.add_notes_to_block_exam_subdeck"
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to conflict screen directly
            conflict_date = date.today() + timedelta(days=7)
            dialog._show_subdeck_conflict_screen("Existing Exam", conflict_date)
            qtbot.wait(100)

            # Should show conflict screen elements
            assert hasattr(dialog, "merge_button")
            assert hasattr(dialog, "rename_button")
            assert hasattr(dialog, "back_button")

            # Click merge button
            qtbot.mouseClick(dialog.merge_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should add notes to existing subdeck
            mock_add_notes.assert_called_once_with("existing-id", self.note_ids)

            # Dialog should be closed
            qtbot.wait_until(lambda: not dialog.isVisible(), timeout=1000)

    def test_conflict_screen_rename_option(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test conflict screen rename option workflow."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog
        from ankihub.main.block_exam_subdecks import BlockExamSubdeck

        existing_subdecks = [
            BlockExamSubdeck(
                subdeck_id="existing-id",
                name="Existing Exam",
                due_date=date.today() + timedelta(days=5),
            ),
        ]

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=existing_subdecks,
            )
            mock_create = mocker.patch(
                "ankihub.main.block_exam_subdecks.create_block_exam_subdeck",
                return_value=BlockExamSubdeck(
                    subdeck_id="new-id",
                    name="Existing Exam (2)",
                    due_date=date.today() + timedelta(days=7),
                ),
            )
            mock_add_notes = mocker.patch(
                "ankihub.main.block_exam_subdecks.add_notes_to_block_exam_subdeck"
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to conflict screen directly
            conflict_date = date.today() + timedelta(days=7)
            dialog._show_subdeck_conflict_screen("Existing Exam", conflict_date)
            qtbot.wait(100)

            # Click rename button
            qtbot.mouseClick(dialog.rename_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should create new subdeck with auto-generated name
            mock_create.assert_called_once_with("Existing Exam (2)", conflict_date)
            mock_add_notes.assert_called_once_with("new-id", self.note_ids)

            # Dialog should be closed
            qtbot.wait_until(lambda: not dialog.isVisible(), timeout=1000)

    def test_dialog_closes_properly(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test dialog closes properly after completion."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Dialog should be visible initially
            assert dialog.isVisible()

            # Simulate successful completion by calling accept
            dialog.accept()
            qtbot.wait(100)

            # Dialog should be closed
            assert not dialog.isVisible()

    def test_dialog_cleanup_on_close(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test dialog properly cleans up resources on close."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Close dialog with reject (cancel/X button)
            dialog.reject()
            qtbot.wait(100)

            # Verify dialog is properly closed
            assert not dialog.isVisible()

            # Close all dialogs to prevent issues during teardown
            dialogs.closeAll()

    def test_error_handling_during_creation(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        """Test error handling during subdeck creation."""
        from ankihub.gui.block_exam_dialog import BlockExamSubdeckDialog

        with anki_session_with_addon_data.profile_loaded():
            mocker.patch(
                "ankihub.main.block_exam_subdecks.get_existing_block_exam_subdecks",
                return_value=[],
            )
            
            # Mock create function to raise unexpected error
            mock_create = mocker.patch(
                "ankihub.main.block_exam_subdecks.create_block_exam_subdeck",
                side_effect=Exception("Unexpected error"),
            )

            # Mock error display
            mock_show_error = mocker.patch("ankihub.gui.utils.show_error_dialog")

            dialog = BlockExamSubdeckDialog(self.note_ids, parent=None)
            qtbot.addWidget(dialog)

            # Go to create screen and fill valid data
            qtbot.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)
            
            dialog.name_input.setText("Test Exam")
            future_date = date.today() + timedelta(days=7)
            dialog.date_input.setDate(future_date)
            qtbot.wait(100)

            # Click finish button
            qtbot.mouseClick(dialog.finish_button, Qt.MouseButton.LeftButton)
            qtbot.wait(100)

            # Should show error dialog
            mock_show_error.assert_called_once()