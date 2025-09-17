"""Tests for block exam subdeck functionality."""

import uuid
import pytest
from datetime import date, timedelta

import aqt
from anki.decks import DeckId
from pytest_anki import AnkiSession

from ankihub.main.block_exam_subdecks import (
    create_block_exam_subdeck,
    add_notes_to_block_exam_subdeck,
    get_existing_block_exam_subdecks,
    validate_subdeck_name,
    validate_due_date,
)
from ankihub.settings import config, BlockExamSubdeckConfig

from tests.fixtures import InstallAHDeck


class TestBlockExamSubdecks:
    def test_create_block_exam_subdeck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Test creating a new subdeck
            due_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
            subdeck_name, was_renamed = create_block_exam_subdeck(
                ah_did, "Test Subdeck", due_date
            )
            
            assert subdeck_name == "Test Subdeck"
            assert not was_renamed
            
            # Verify subdeck was created
            deck_config = config.deck_config(ah_did)
            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            full_name = f"{anki_deck_name}::Test Subdeck"
            subdeck = aqt.mw.col.decks.by_name(full_name)
            assert subdeck is not None
            
            # Verify configuration was saved
            saved_due_date = config.get_block_exam_subdeck_due_date(
                str(ah_did), str(subdeck["id"])
            )
            assert saved_due_date == due_date
    
    def test_create_subdeck_with_conflict(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Create first subdeck
            subdeck_name1, was_renamed1 = create_block_exam_subdeck(ah_did, "Test Subdeck")
            assert subdeck_name1 == "Test Subdeck"
            assert not was_renamed1
            
            # Create second subdeck with same name
            subdeck_name2, was_renamed2 = create_block_exam_subdeck(ah_did, "Test Subdeck")
            assert subdeck_name2 == "Test Subdeck (1)"
            assert was_renamed2
    
    def test_create_subdeck_without_due_date(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Create subdeck without due date
            subdeck_name, was_renamed = create_block_exam_subdeck(ah_did, "No Date Subdeck")
            assert subdeck_name == "No Date Subdeck"
            assert not was_renamed
            
            # Verify subdeck was created but no configuration was saved
            deck_config = config.deck_config(ah_did)
            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            full_name = f"{anki_deck_name}::No Date Subdeck"
            subdeck = aqt.mw.col.decks.by_name(full_name)
            assert subdeck is not None
            
            saved_due_date = config.get_block_exam_subdeck_due_date(
                str(ah_did), str(subdeck["id"])
            )
            assert saved_due_date is None
    
    def test_add_notes_to_block_exam_subdeck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Create a subdeck first
            due_date = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
            subdeck_name, _ = create_block_exam_subdeck(ah_did, "Notes Subdeck", due_date)
            
            # Create some notes
            deck_config = config.deck_config(ah_did)
            note_type = aqt.mw.col.models.by_name("Basic")
            note1 = aqt.mw.col.new_note(note_type)
            note1["Front"] = "Test 1"
            aqt.mw.col.add_note(note1, deck_config.anki_id)
            
            note2 = aqt.mw.col.new_note(note_type)
            note2["Front"] = "Test 2"
            aqt.mw.col.add_note(note2, deck_config.anki_id)
            
            note_ids = [note1.id, note2.id]
            
            # Add notes to subdeck with new due date
            new_due_date = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
            add_notes_to_block_exam_subdeck(
                ah_did, subdeck_name, note_ids, new_due_date
            )
            
            # Verify notes were moved to subdeck
            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            full_subdeck_name = f"{anki_deck_name}::{subdeck_name}"
            subdeck = aqt.mw.col.decks.by_name(full_subdeck_name)
            
            for note_id in note_ids:
                note = aqt.mw.col.get_note(note_id)
                cards = note.cards()
                for card in cards:
                    # Check if card is in the subdeck (either did or odid)
                    assert card.did == subdeck["id"] or card.odid == subdeck["id"]
            
            # Verify configuration was updated with new due date
            saved_due_date = config.get_block_exam_subdeck_due_date(
                str(ah_did), str(subdeck["id"])
            )
            assert saved_due_date == new_due_date
    
    def test_get_existing_block_exam_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Initially should return empty list
            existing = get_existing_block_exam_subdecks(ah_did)
            assert existing == []
            
            # Create some block exam subdecks
            due_date1 = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
            subdeck_name1, _ = create_block_exam_subdeck(ah_did, "Exam 1", due_date1)
            
            due_date2 = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
            subdeck_name2, _ = create_block_exam_subdeck(ah_did, "Exam 2", due_date2)
            
            # Should now return both subdecks
            existing = get_existing_block_exam_subdecks(ah_did)
            assert len(existing) == 2
            
            subdeck_names = [name for name, _ in existing]
            assert "Exam 1" in subdeck_names
            assert "Exam 2" in subdeck_names
    
    def test_get_existing_block_exam_subdecks_with_regular_subdecks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            deck_config = config.deck_config(ah_did)
            anki_deck_name = aqt.mw.col.decks.name_if_exists(deck_config.anki_id)
            
            # Create a regular subdeck (not block exam)
            regular_subdeck_name = f"{anki_deck_name}::Regular Subdeck"
            aqt.mw.col.decks.add_normal_deck_with_name(regular_subdeck_name)
            
            # Create a block exam subdeck
            due_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
            block_exam_subdeck_name, _ = create_block_exam_subdeck(ah_did, "Block Exam", due_date)
            
            # Should only return the block exam subdeck
            existing = get_existing_block_exam_subdecks(ah_did)
            assert len(existing) == 1
            assert existing[0][0] == "Block Exam"
    
    def test_validate_subdeck_name(self):
        # Valid names
        assert validate_subdeck_name("Valid Name")
        assert validate_subdeck_name("Test123")
        assert validate_subdeck_name("Name with spaces")
        assert validate_subdeck_name("Name_with_underscores")
        assert validate_subdeck_name("Name-with-hyphens")
        
        # Invalid names
        assert not validate_subdeck_name("")
        assert not validate_subdeck_name("   ")  # Only whitespace
        assert not validate_subdeck_name("Invalid:Name")  # Contains colon
        assert not validate_subdeck_name("Invalid<Name")  # Contains less than
        assert not validate_subdeck_name("Invalid>Name")  # Contains greater than
        assert not validate_subdeck_name('Invalid"Name')  # Contains quote
        assert not validate_subdeck_name("Invalid|Name")  # Contains pipe
        assert not validate_subdeck_name("Invalid?Name")  # Contains question mark
        assert not validate_subdeck_name("Invalid*Name")  # Contains asterisk
        assert not validate_subdeck_name("Invalid/Name")  # Contains forward slash
        assert not validate_subdeck_name("Invalid\\Name")  # Contains backslash
    
    def test_validate_due_date(self):
        # Valid future dates
        future_date1 = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        future_date7 = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
        assert validate_due_date(future_date1)
        assert validate_due_date(future_date7)
        
        # Invalid past dates
        past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        assert not validate_due_date(past_date)
        
        # Invalid today (not in future)
        today = date.today().strftime("%Y-%m-%d")
        assert not validate_due_date(today)
        
        # Invalid formats
        assert not validate_due_date("invalid-date")
        assert not validate_due_date("2023-13-01")  # Invalid month
        assert not validate_due_date("2023-01-32")  # Invalid day
        assert not validate_due_date("01-01-2023")  # Wrong format
        assert not validate_due_date("")
        assert not validate_due_date("2023/01/01")  # Wrong separator
    
    def test_config_methods(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Test initially empty
            assert config.get_block_exam_subdecks() == []
            
            # Test adding configuration
            config_item = BlockExamSubdeckConfig(
                ankihub_deck_id="test-deck-id",
                subdeck_id="test-subdeck-id",
                due_date="2024-12-31"
            )
            config.add_block_exam_subdeck(config_item)
            
            # Test retrieving configuration
            configs = config.get_block_exam_subdecks()
            assert len(configs) == 1
            assert configs[0].ankihub_deck_id == "test-deck-id"
            assert configs[0].subdeck_id == "test-subdeck-id"
            assert configs[0].due_date == "2024-12-31"
            
            # Test getting due date
            due_date = config.get_block_exam_subdeck_due_date("test-deck-id", "test-subdeck-id")
            assert due_date == "2024-12-31"
            
            # Test getting due date for non-existent
            due_date = config.get_block_exam_subdeck_due_date("non-existent", "non-existent")
            assert due_date is None
            
            # Test updating existing configuration
            updated_config = BlockExamSubdeckConfig(
                ankihub_deck_id="test-deck-id",
                subdeck_id="test-subdeck-id",
                due_date="2025-01-15"
            )
            config.add_block_exam_subdeck(updated_config)
            
            # Should still only have one config but with updated date
            configs = config.get_block_exam_subdecks()
            assert len(configs) == 1
            assert configs[0].due_date == "2025-01-15"
    
    def test_create_subdeck_deck_not_found(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Try to create subdeck for non-existent deck
            fake_ah_did = uuid.uuid4()
            with pytest.raises(ValueError, match="Deck config not found"):
                create_block_exam_subdeck(fake_ah_did, "Test Subdeck")
    
    def test_add_notes_deck_not_found(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        with anki_session_with_addon_data.profile_loaded():
            # Try to add notes for non-existent deck
            fake_ah_did = uuid.uuid4()
            with pytest.raises(ValueError, match="Deck config not found"):
                add_notes_to_block_exam_subdeck(fake_ah_did, "Non-existent", [1, 2, 3])
    
    def test_add_notes_subdeck_not_found(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            
            # Try to add notes to non-existent subdeck
            with pytest.raises(ValueError, match="Subdeck .* not found"):
                add_notes_to_block_exam_subdeck(ah_did, "Non-existent Subdeck", [1, 2, 3])