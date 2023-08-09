import cProfile
import csv
import gzip
import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Protocol

import pytest
from anki.models import NotetypeDict, NotetypeId
from pytest_anki import AnkiSession

from ankihub.ankihub_client import NoteInfo
from ankihub.ankihub_client.ankihub_client import CSV_DELIMITER, _transform_notes_data

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.main.importing import AnkiHubImporter

ANKING_DECK_CSV_GZ = Path(__file__).parent / "deck_anking.csv.gz"
ANKING_NOTE_TYPES_JSON = Path(__file__).parent / "anking_note_types.json"
PROFILING_STATS_DIR = Path(__file__).parent / "profiling_stats"


@pytest.fixture
def current_test_name(request) -> str:
    return request.node.name


class WriteProfilingStats(Protocol):
    def __call__(self, profiler: cProfile.Profile) -> None:
        ...


@pytest.fixture
def write_profiling_stats(current_test_name) -> WriteProfilingStats:
    """Write the profiling stats to a file in the profiling stats directory.
    The file is named after the current test name."""

    def _write_profiling_stats(profiler: cProfile.Profile) -> None:
        stats_path = PROFILING_STATS_DIR / f"{current_test_name}.pstats"
        profiler.dump_stats(stats_path)

    return _write_profiling_stats


def test_anking_deck_first_time_import(
    anki_session_with_addon_data: AnkiSession,
    next_deterministic_uuid: Callable[[], uuid.UUID],
    write_profiling_stats: WriteProfilingStats,
):
    """Test that importing a portion of the AnKing deck takes less than a threshold duration."""
    with anki_session_with_addon_data.profile_loaded():

        # Use the first 100 notes for profiling
        notes_data = notes_data_from_csv_gz(ANKING_DECK_CSV_GZ)
        notes_data = notes_data[:100]

        note_types = note_types_from_json(ANKING_NOTE_TYPES_JSON)
        importer = AnkiHubImporter()

        # Start profiling
        profiler = cProfile.Profile()
        profiler.enable()

        start_time = time.time()

        importer._import_ankihub_deck_inner(
            ankihub_did=next_deterministic_uuid(),
            notes_data=notes_data,
            deck_name="test",
            is_first_import_of_deck=True,
            remote_note_types=note_types,
            protected_fields={},
            protected_tags=[],
        )

        profiler.disable()

        elapsed_time = time.time() - start_time
        assert elapsed_time < 1

        # Save the profiling results
        write_profiling_stats(profiler)


def notes_data_from_csv_gz(csv_path: Path) -> list[NoteInfo]:
    content = csv_path.read_bytes()
    if csv_path.suffix == ".gz":
        deck_csv_content = gzip.decompress(content).decode("utf-8")
    else:
        deck_csv_content = content.decode("utf-8")

    reader = csv.DictReader(
        deck_csv_content.splitlines(), delimiter=CSV_DELIMITER, quotechar="'"
    )
    notes_data_raw = [row for row in reader]
    notes_data_raw = _transform_notes_data(notes_data_raw)
    reuslt = [NoteInfo.from_dict(row) for row in notes_data_raw]
    return reuslt


def note_types_from_json(json_path: Path) -> dict[NotetypeId, NotetypeDict]:
    result = json.loads(json_path.read_text())
    return result
