import os
import uuid
from typing import Callable, Dict, List

import pytest
from anki.models import NotetypeDict, NotetypeId
from pytest_anki import AnkiSession

from ankihub.ankihub_client import NoteInfo
from ankihub.settings import DeckConfig

from .conftest import Profile

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.main.importing import AnkiHubImporter


@pytest.mark.performance
def test_anking_deck_first_time_import(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    anking_notes_data: List[NoteInfo],
    anking_note_types: Dict[NotetypeId, NotetypeDict],
    profile: Profile,
):
    """Test that importing a portion of the AnKing deck takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():
        notes_data = anking_notes_data[:100]
        importer = AnkiHubImporter()
        duration = profile(
            lambda: importer.import_ankihub_deck(
                ankihub_did=next_deterministic_uuid(),
                notes=notes_data,
                deck_name="test",
                is_first_import_of_deck=True,
                note_types=anking_note_types,
                protected_fields={},
                protected_tags=[],
                suspend_new_cards_of_new_notes=False,
                suspend_new_cards_of_existing_notes=DeckConfig.suspend_new_cards_of_existing_notes_default(),
            )
        )
        print(f"Importing {len(notes_data)} notes took {duration} seconds")
        assert duration < 0.5
