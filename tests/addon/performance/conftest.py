import cProfile
from pathlib import Path
from typing import Protocol

import pytest

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
