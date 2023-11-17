import cProfile
import csv
import gzip
import json
import pstats
from pathlib import Path
from typing import Any, Callable, Dict, List, Protocol

import pytest
from anki.models import NotetypeDict, NotetypeId

from ankihub.ankihub_client import NoteInfo
from ankihub.ankihub_client.ankihub_client import CSV_DELIMITER, _transform_notes_data

PROFILING_STATS_DIR = Path(__file__).parent / "profiling_stats"

ANKING_DECK_CSV_GZ = Path(__file__).parent / "deck_anking.csv.gz"
ANKING_NOTE_TYPES_JSON = Path(__file__).parent / "anking_note_types.json"


@pytest.fixture
def anking_notes_data() -> List[NoteInfo]:
    notes_data = notes_data_from_csv_gz(ANKING_DECK_CSV_GZ)
    return notes_data


@pytest.fixture
def anking_note_types() -> Dict[NotetypeId, NotetypeDict]:
    note_types = note_types_from_json(ANKING_NOTE_TYPES_JSON)
    return note_types


def notes_data_from_csv_gz(csv_path: Path) -> List[NoteInfo]:
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


class WriteProfilingStats(Protocol):
    def __call__(self, profiler: cProfile.Profile) -> None:
        ...


@pytest.fixture
def write_profiling_stats(
    current_test_parent_node_name: str, current_test_name: str
) -> WriteProfilingStats:
    """Write the profiling stats to a file in the profiling stats directory.
    The file is named after the current test name and its parent node."""

    def _write_profiling_stats(profiler: cProfile.Profile) -> None:
        stats_path = (
            PROFILING_STATS_DIR
            / f"{current_test_parent_node_name.split('.')[0]}_{current_test_name}.pstats"
        )
        profiler.dump_stats(stats_path)

    return _write_profiling_stats


@pytest.fixture
def current_test_name(request) -> str:
    return request.node.name


@pytest.fixture
def current_test_parent_node_name(request) -> str:
    return request.node.parent.name


class Profile(Protocol):
    def __call__(self, func: Callable[[], Any]) -> float:
        ...


@pytest.fixture
def profile(write_profiling_stats: WriteProfilingStats) -> Profile:
    """Profile the given function and write the profiling stats to a file in the profiling stats directory.
    The file is named after the current test name.
    Return the cumulative time in seconds."""

    def _profile(func: Callable[[], Any]) -> float:
        profiler = cProfile.Profile()
        profiler.enable()
        func()
        profiler.disable()

        write_profiling_stats(profiler)

        # Get the function with the highest cumulative time which is the total time spent in the function
        stats = pstats.Stats(profiler).strip_dirs()
        highest_cum_time_func = max(stats.stats.items(), key=lambda x: x[1][3])  # type: ignore
        total_cum_time = highest_cum_time_func[1][3]

        return total_cum_time

    return _profile
