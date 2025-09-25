"""Tests for block_exam_subdecks module functions not covered in integration tests."""

import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from ankihub.main.block_exam_subdecks import (
    check_and_handle_block_exam_subdeck_due_dates,
    check_block_exam_subdeck_due_dates,
    trigger_due_date_dialog,
    validate_due_date,
)
from ankihub.settings import BlockExamSubdeckConfig, config
from tests.fixtures import AnkiSession


class TestCheckBlockExamSubdeckDueDates:
    """Tests for check_block_exam_subdeck_due_dates function."""

    def test_check_block_exam_subdeck_due_dates_no_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test function returns empty list when no subdecks exist."""
        with anki_session_with_addon_data.profile_loaded():
            # Ensure no existing configurations
            config._private_config.block_exam_subdecks = []

            expired = check_block_exam_subdeck_due_dates()
            assert expired == []

    def test_check_block_exam_subdeck_due_dates_none_expired(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test function returns empty list when no subdecks are expired."""
        with anki_session_with_addon_data.profile_loaded():
            # Create future due dates
            future_date1 = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            future_date2 = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")

            config_items = [
                BlockExamSubdeckConfig(
                    ankihub_deck_id=str(uuid.uuid4()), subdeck_id=str(uuid.uuid4()), due_date=future_date1
                ),
                BlockExamSubdeckConfig(
                    ankihub_deck_id=str(uuid.uuid4()), subdeck_id=str(uuid.uuid4()), due_date=future_date2
                ),
            ]

            # Add configurations
            for config_item in config_items:
                config.add_block_exam_subdeck(config_item)

            expired = check_block_exam_subdeck_due_dates()
            assert expired == []

    def test_check_block_exam_subdeck_due_dates_with_expired(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test function identifies expired subdecks correctly."""
        with anki_session_with_addon_data.profile_loaded():
            # Create past, today, and future due dates
            past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            today_date = date.today().strftime("%Y-%m-%d")
            future_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

            config_items = [
                BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date=past_date),
                BlockExamSubdeckConfig(
                    ankihub_deck_id="deck2",
                    subdeck_id="subdeck2",
                    due_date=today_date,  # Today counts as expired (>= today)
                ),
                BlockExamSubdeckConfig(ankihub_deck_id="deck3", subdeck_id="subdeck3", due_date=future_date),
            ]

            # Add configurations
            for config_item in config_items:
                config.add_block_exam_subdeck(config_item)

            expired = check_block_exam_subdeck_due_dates()

            # Should return the two expired subdecks
            assert len(expired) == 2
            expired_deck_ids = [config.ankihub_deck_id for config in expired]
            assert "deck1" in expired_deck_ids  # past date
            assert "deck2" in expired_deck_ids  # today's date
            assert "deck3" not in expired_deck_ids  # future date

    def test_check_block_exam_subdeck_due_dates_with_invalid_dates(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test function handles invalid date formats gracefully."""
        with anki_session_with_addon_data.profile_loaded():
            # Create configurations with invalid dates
            config_items = [
                BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date="invalid-date"),
                BlockExamSubdeckConfig(
                    ankihub_deck_id="deck2",
                    subdeck_id="subdeck2",
                    due_date="2023-13-01",  # Invalid month
                ),
                BlockExamSubdeckConfig(
                    ankihub_deck_id="deck3",
                    subdeck_id="subdeck3",
                    due_date="",  # Empty string
                ),
            ]

            # Add configurations
            for config_item in config_items:
                config.add_block_exam_subdeck(config_item)

            expired = check_block_exam_subdeck_due_dates()

            # Should return empty list since all dates are invalid
            assert expired == []

    def test_check_block_exam_subdeck_due_dates_mixed_valid_invalid(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test function handles mix of valid and invalid dates."""
        with anki_session_with_addon_data.profile_loaded():
            # Mix of valid expired, valid future, and invalid dates
            past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            future_date = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

            config_items = [
                BlockExamSubdeckConfig(ankihub_deck_id="valid_expired", subdeck_id="subdeck1", due_date=past_date),
                BlockExamSubdeckConfig(ankihub_deck_id="valid_future", subdeck_id="subdeck2", due_date=future_date),
                BlockExamSubdeckConfig(ankihub_deck_id="invalid_date", subdeck_id="subdeck3", due_date="not-a-date"),
            ]

            # Add configurations
            for config_item in config_items:
                config.add_block_exam_subdeck(config_item)

            expired = check_block_exam_subdeck_due_dates()

            # Should return only the valid expired subdeck
            assert len(expired) == 1
            assert expired[0].ankihub_deck_id == "valid_expired"


class TestTriggerDueDateDialog:
    """Tests for trigger_due_date_dialog function."""

    def test_trigger_due_date_dialog_empty_list(self):
        """Test function does nothing with empty expired subdecks list."""
        # Should not raise any exception or show dialog
        trigger_due_date_dialog([])
        # No assertions needed - just ensuring it doesn't crash

    @patch("aqt.utils.showInfo")
    def test_trigger_due_date_dialog_with_expired_subdecks(self, mock_showinfo):
        """Test function shows info dialog with expired subdecks count."""
        expired_subdecks = [
            BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date="2023-01-01"),
            BlockExamSubdeckConfig(ankihub_deck_id="deck2", subdeck_id="subdeck2", due_date="2023-01-02"),
        ]

        trigger_due_date_dialog(expired_subdecks)

        # Should call showInfo with appropriate message
        mock_showinfo.assert_called_once_with("2 block exam subdeck(s) have reached their due date.")

    @patch("aqt.utils.showInfo")
    def test_trigger_due_date_dialog_single_expired(self, mock_showinfo):
        """Test function shows correct singular message for one expired subdeck."""
        expired_subdecks = [
            BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date="2023-01-01"),
        ]

        trigger_due_date_dialog(expired_subdecks)

        # Should call showInfo with appropriate message
        mock_showinfo.assert_called_once_with("1 block exam subdeck(s) have reached their due date.")


class TestCheckAndHandleBlockExamSubdeckDueDates:
    """Tests for check_and_handle_block_exam_subdeck_due_dates function."""

    @patch("ankihub.main.block_exam_subdecks.trigger_due_date_dialog")
    @patch("ankihub.main.block_exam_subdecks.check_block_exam_subdeck_due_dates")
    def test_check_and_handle_no_expired_subdecks(
        self,
        mock_check_due_dates,
        mock_trigger_dialog,
    ):
        """Test function does not trigger dialog when no subdecks are expired."""
        mock_check_due_dates.return_value = []

        check_and_handle_block_exam_subdeck_due_dates()

        mock_check_due_dates.assert_called_once()

        mock_trigger_dialog.assert_not_called()

    @patch("ankihub.main.block_exam_subdecks.trigger_due_date_dialog")
    @patch("ankihub.main.block_exam_subdecks.check_block_exam_subdeck_due_dates")
    def test_check_and_handle_with_expired_subdecks(
        self,
        mock_check_due_dates,
        mock_trigger_dialog,
    ):
        """Test function triggers dialog when expired subdecks are found."""
        # Mock expired subdecks
        expired_subdecks = [
            BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date="2023-01-01"),
        ]
        mock_check_due_dates.return_value = expired_subdecks

        check_and_handle_block_exam_subdeck_due_dates()

        mock_check_due_dates.assert_called_once()

        mock_trigger_dialog.assert_called_once_with(expired_subdecks)

    @patch("ankihub.main.block_exam_subdecks.trigger_due_date_dialog")
    @patch("ankihub.main.block_exam_subdecks.check_block_exam_subdeck_due_dates")
    def test_check_and_handle_with_exception(
        self,
        mock_check_due_dates,
        mock_trigger_dialog,
    ):
        """Test function handles exceptions gracefully and logs errors."""
        mock_check_due_dates.side_effect = Exception("Test error")

        check_and_handle_block_exam_subdeck_due_dates()

        mock_check_due_dates.assert_called_once()

        mock_trigger_dialog.assert_not_called()

    @patch("ankihub.main.block_exam_subdecks.trigger_due_date_dialog")
    @patch("ankihub.main.block_exam_subdecks.check_block_exam_subdeck_due_dates")
    def test_check_and_handle_trigger_dialog_exception(
        self,
        mock_check_due_dates,
        mock_trigger_dialog,
    ):
        """Test function handles exceptions in trigger_due_date_dialog gracefully."""
        expired_subdecks = [
            BlockExamSubdeckConfig(ankihub_deck_id="deck1", subdeck_id="subdeck1", due_date="2023-01-01"),
        ]
        mock_check_due_dates.return_value = expired_subdecks

        mock_trigger_dialog.side_effect = Exception("Dialog error")

        check_and_handle_block_exam_subdeck_due_dates()

        mock_check_due_dates.assert_called_once()

        mock_trigger_dialog.assert_called_once_with(expired_subdecks)


class TestValidateDueDateExtended:
    """Additional tests for validate_due_date function beyond what's in integration tests."""

    def test_validate_due_date_edge_cases(self):
        """Test validate_due_date with additional edge cases."""
        # Test leap year dates (using future leap year)
        assert validate_due_date("2028-02-29")  # Valid leap year date in future
        assert not validate_due_date("2023-02-29")  # Invalid non-leap year date

        # Test None and non-string inputs would cause TypeError if passed
        # (function expects string, but we'll test behavior if called incorrectly)
        with pytest.raises(TypeError):
            validate_due_date(None)

        # Test various invalid formats
        assert not validate_due_date("2023-1-1")  # Single digit month/day
        assert not validate_due_date("23-01-01")  # Two digit year
        assert not validate_due_date("2023-01-01 00:00:00")  # With time
        assert not validate_due_date(" 2023-01-01 ")  # With whitespace
        assert not validate_due_date("2023-01-01T00:00:00")  # ISO format with time

    def test_validate_due_date_boundary_conditions(self):
        """Test validate_due_date with boundary date conditions."""
        today = date.today()

        # Test yesterday (should be False)
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        assert not validate_due_date(yesterday)

        # Test today (should be False - not in future)
        today_str = today.strftime("%Y-%m-%d")
        assert not validate_due_date(today_str)

        # Test tomorrow (should be True)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        assert validate_due_date(tomorrow)

        # Test far future date
        far_future = (today + timedelta(days=365 * 10)).strftime("%Y-%m-%d")
        assert validate_due_date(far_future)
