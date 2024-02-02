import os
import uuid
from typing import Callable, Dict, List

import pytest
from anki.models import NotetypeDict, NotetypeId
from pytest_anki import AnkiSession

from .conftest import Profile

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import NoteInfo
from ankihub.main.exporting import to_note_data
from ankihub.main.importing import AnkiHubImporter
from ankihub.settings import DeckConfig, DeleteNoteOnRemoteDelete


@pytest.mark.performance
def test_anking_export_without_changes(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    anking_notes_data: List[NoteInfo],
    anking_note_types: Dict[NotetypeId, NotetypeDict],
    profile: Profile,
):
    """Test that exporting a portion of the AnKing deck takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        notes_amount = 100

        # Import notes
        notes_data = anking_notes_data[:notes_amount]
        importer = AnkiHubImporter()
        importer.import_ankihub_deck(
            ankihub_did=next_deterministic_uuid(),
            notes=notes_data,
            deck_name="test",
            is_first_import_of_deck=True,
            delete_note_on_remote_delete=DeleteNoteOnRemoteDelete.NEVER,
            note_types=anking_note_types,
            protected_fields={},
            protected_tags=[],
            suspend_new_cards_of_new_notes=False,
            suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
        )

        # Assert that exporting the notes takes less than 0.3 seconds
        notes = [mw.col.get_note(nid) for nid in mw.col.find_notes("")]
        assert len(notes) == notes_amount  # sanity check

        def export_notes():
            for note in notes:
                to_note_data(note)

        duration = profile(export_notes)
        print(f"Exporting {len(notes)} notes took {duration} seconds")
        assert duration < 0.2
