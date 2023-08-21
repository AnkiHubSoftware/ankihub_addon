import cProfile
import os
import time
import uuid
from typing import Callable, Dict, List

from anki.models import NotetypeDict, NotetypeId
from pytest_anki import AnkiSession

from ankihub.ankihub_client import NoteInfo

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.main.importing import AnkiHubImporter

from .conftest import WriteProfilingStats


def test_anking_deck_first_time_import(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    anking_notes_data: List[NoteInfo],
    anking_note_types: Dict[NotetypeId, NotetypeDict],
    write_profiling_stats: WriteProfilingStats,
):
    """Test that importing a portion of the AnKing deck takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():

        # Use the first 100 notes for profiling
        notes_data = anking_notes_data[:100]

        note_types = anking_note_types
        importer = AnkiHubImporter()

        # Start profiling
        profiler = cProfile.Profile()
        profiler.enable()

        start_time = time.time()

        importer.import_ankihub_deck(
            ankihub_did=next_deterministic_uuid(),
            notes=notes_data,
            deck_name="test",
            is_first_import_of_deck=True,
            note_types=note_types,
            protected_fields={},
            protected_tags=[],
        )

        profiler.disable()

        elapsed_time = time.time() - start_time

        # Save the profiling results
        write_profiling_stats(profiler)

        assert elapsed_time < 0.5
