import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Type

import pytest
from coverage.exceptions import DataError  # type: ignore
from pytest import FixtureRequest, MonkeyPatch
from pytest_anki import AnkiSession
from pytest_anki.plugin import anki_running
from pytestqt.qtbot import QtBot  # type: ignore
from requests_mock import Mocker

from ..fixtures import (  # noqa F401
    add_anki_note,
    ankihub_basic_note_type,
    import_ah_note,
    import_ah_note_type,
    import_ah_notes,
    install_ah_deck,
    latest_instance_tracker,
    mock_download_and_install_deck_dependencies,
    mock_message_box_with_cb,
    mock_show_dialog_with_cb,
    mock_study_deck_dialog_with_cb,
    mock_suggestion_dialog,
    next_deterministic_id,
    next_deterministic_uuid,
    set_feature_flag_state,
)

try:
    import pytest_forked
except Exception:  # pragma: no cover
    pytest_forked = None


# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client.ankihub_client import AnkiHubClient

REPO_ROOT_PATH = Path(__file__).absolute().parent.parent.parent

# id of the Anki profile used for testing
# it is used as the name of the profile data folder in the user_files folder
TEST_PROFILE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def anki_session(request: FixtureRequest, qtbot: QtBot) -> Generator[AnkiSession, None, None]:
    """Overwrites the anki_session fixture from pytest-anki to disable web debugging by default.
    This is done because web debugging is not used in any tests and it sometimes leads to errors.
    Otherwise the fixture is the same as the original.
    """
    default_parameters = {"enable_web_debugging": False}
    indirect_parameters: Optional[Dict[str, Any]] = getattr(request, "param", {})
    merged_parameters: Optional[Dict[str, Any]] = {
        **default_parameters,
        **indirect_parameters,
    }

    with anki_running(qtbot=qtbot, **merged_parameters) as session:
        yield session


# autouse=True is set so that the tests don't fail because without it mw is None
# when imported from aqt (from aqt import mw)
# and some add-ons file use mw when you import them
# this is a workaround for that
# it might be good to change the add-ons file to not do that instead of using autouse=True
# the requests_mock argument is here to disallow real requests for all tests that use the fixture
# to prevent hidden real requests
@pytest.fixture(scope="function", autouse=True)
def anki_session_with_addon_data(
    anki_session: AnkiSession,
    requests_mock: Mocker,
    monkeypatch: MonkeyPatch,
) -> Generator[AnkiSession, None, None]:
    """Sets up a temporary anki base folder and a temporary ankihub base folder.
    This is a replacement for running the whole initialization process of the add-on
    which is not needed for most tests (test can call entrypoint.run manually if they need to).
    By setting up the temporary folders for each test the user's Anki/AnkiHub data is not modified
    and the tests can be run in parallel.
    The add-ons code is not copied into the add-on's folder in the temporary anki_base folder.
    Instead the tests run the code in the ankihub folder of the repo.
    """
    from ankihub.entry_point import _profile_setup
    from ankihub.settings import config, setup_logger

    # Add the add-ons public config to Anki
    config_path = REPO_ROOT_PATH / "ankihub" / "config.json"
    with open(config_path) as f:
        config_dict = json.load(f)
    anki_session.create_addon_config(package_name="ankihub", default_config=config_dict)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Change the ankihub base path to a temporary folder to isolate the tests
        os.environ["ANKIHUB_BASE_PATH"] = tmpdir

        config.setup_public_config_and_other_settings()
        setup_logger()

        with monkeypatch.context() as m:
            # monkeypatch the uuid4 function to always return the same value so
            # the profile data folder is always the same
            m.setattr("uuid.uuid4", lambda: TEST_PROFILE_ID)
            with anki_session.profile_loaded():
                _profile_setup()

        _mock_all_feature_flags_to_default_values(monkeypatch)
        _mock_user_details(requests_mock)

        yield anki_session


def _mock_all_feature_flags_to_default_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(AnkiHubClient, "get_feature_flags", lambda *args, **kwargs: {})


def _mock_user_details(request_mock: Mocker) -> None:
    request_mock.get(
        "https://app.ankihub.net/api/users/me",
        json={
            "has_flashcard_selector_access": False,
            "has_reviewer_extension_access": False,
            "id": 123,
            "username": "test_user",
        },
    )


@pytest.fixture
def anki_session_with_addon_before_profile_support(
    anki_session_with_addon_data: AnkiSession,
):
    # previous versions of the add-on didn't support multiple Anki profiles and
    # had one set of data for all profiles
    # this fixtures simulates the data structure of such an add-on version
    anki_session: AnkiSession = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw

        # Set up the user_files folder with test data
        user_files_path = Path(mw.addonManager.addonsFolder("ankihub")) / "user_files"

        if user_files_path.exists():
            shutil.rmtree(user_files_path)

        shutil.copytree(
            REPO_ROOT_PATH / "tests" / "addon" / "profile_migration_test_data",
            user_files_path,
        )

    yield anki_session


@pytest.fixture(autouse=True)
def set_call_on_profile_did_open_on_maybe_auto_sync_to_false(monkeypatch):
    # Anki calls maybe_auto_sync_on_open_close in AnkiQt.loadProfile.
    # However, when running tests, pytest-anki AnkiSession.profile_loaded does not
    # call maybe_auto_sync_on_open_close.
    # Because of this, we need to set CALL_ON_PROFILE_DID_OPEN_ON_MAYBE_AUTO_SYNC to False.
    # Then _on_profile_did_open is called, because AnkiSession.profile_loaded calls profile_did_open.
    monkeypatch.setattr("ankihub.entry_point.CALL_ON_PROFILE_DID_OPEN_ON_MAYBE_AUTO_SYNC", False)


def pytest_set_filtered_exceptions() -> List[Type[Exception]]:
    """Tests which raise one of these will be retried by pytest-retry."""
    return [DataError]


# Crash rerun functionality
# This provides automatic retry for tests that crash (segfault/SIGSEGV) rather than failing normally.
# Usage: pytest --crash-reruns=2 will retry crashed tests up to 2 times.
# The retry information is visible in standard pytest output via report annotations.


def pytest_addoption(parser):
    """Add command-line options for crash rerun functionality."""
    g = parser.getgroup("crash-reruns")
    g.addoption(
        "--crash-reruns",
        action="store",
        type=int,
        default=0,
        help="Rerun a test up to N times only if it crashed (SIGSEGV/segfault).",
    )
    g.addoption(
        "--crash-reruns-delay",
        action="store",
        type=float,
        default=0.0,
        help="Seconds to sleep between crash reruns.",
    )


CRASH_PATTERN = re.compile(
    r"(?i)\b(CRASHED|Segmentation fault|SIGSEGV|Crashed with exit code\s*-?11|"
    r"worker .* (crashed|died)|node down)\b"
)


def _reports_indicate_crash(reports) -> bool:
    """
    Check if test reports indicate a crash (segfault/SIGSEGV) rather than a normal failure.

    Args:
        reports: List of pytest TestReport objects

    Returns:
        True if any report indicates a crash, False otherwise
    """
    for rep in reports or ():
        if getattr(rep, "outcome", None) == "failed":
            text = getattr(rep, "longreprtext", None)
            if not text:
                # fall back to stringifying longrepr if needed
                text = str(getattr(rep, "longrepr", "") or "")
            if CRASH_PATTERN.search(text):
                return True
    return False


def _annotate_reports(reports, text: str) -> None:
    """
    Add a crash-reruns section to test reports to make retry info visible in pytest output.

    This allows crash-rerun information to appear in standard test output without
    needing -s or special flags, making it visible in CI logs and test reports.

    Args:
        reports: List of pytest TestReport objects
        text: Message to display in the crash-reruns section
    """
    for rep in reports or []:
        if hasattr(rep, "sections"):
            rep.sections.append(("crash-reruns", text))


def _run_isolated(item, nextitem):
    """
    Run a single test in a forked subprocess using pytest-forked.

    This provides process isolation so that crashes (segfaults) in one test
    don't bring down the entire test suite.

    Args:
        item: The pytest test item to run
        nextitem: The next test item (may be None)

    Returns:
        List of pytest TestReport objects from the forked test run

    Raises:
        RuntimeError: If pytest-forked is not installed
    """
    if pytest_forked is None:
        # Safety guard: we rely on pytest-forked for isolation.
        raise RuntimeError("pytest-forked must be installed for --crash-reruns")

    fn = pytest_forked.forked_run_report
    sig = None
    try:
        sig = inspect.signature(fn)
    except Exception:
        pass

    # Support both historical and newer signatures.
    if sig and len(sig.parameters) >= 2:
        return fn(item, nextitem)
    else:
        return fn(item)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """
    Hook to handle test execution with crash-aware retries.

    When --crash-reruns is specified, this hook takes over the test protocol
    to automatically retry tests that crash (segfault/SIGSEGV). Normal test
    failures are not retried.

    The retry information is added to test reports and visible in standard
    pytest output under the "crash-reruns" section.

    Args:
        item: The pytest test item to run
        nextitem: The next test item (may be None)

    Returns:
        None to let pytest handle normally (when --crash-reruns=0)
        True to indicate we've handled the protocol (when --crash-reruns>0)
    """
    reruns = item.config.getoption("--crash-reruns")
    if reruns <= 0:
        return  # let pytest handle as usual

    delay = float(item.config.getoption("--crash-reruns-delay") or 0.0)
    attempts_left = int(reruns) + 1  # first run + N retries on crash
    last_reports = None

    while attempts_left > 0:
        attempts_left -= 1
        reports = _run_isolated(item, nextitem)
        last_reports = reports

        if not _reports_indicate_crash(reports):
            break  # normal pass/fail: stop retrying
        if attempts_left <= 0:
            # Annotate final report when retries are exhausted
            _annotate_reports(reports, f"Test crashed after {reruns + 1} attempts; no more retries available")
            break

        tried = (item.config.getoption("--crash-reruns") + 1) - attempts_left
        retry_msg = f"[crash-reruns] {item.nodeid} crashed; retrying (attempt {tried + 1}/{reruns + 1})"

        # Annotate reports to make retry visible in standard output
        _annotate_reports(reports, f"Test crashed; scheduling retry (attempt {tried + 1}/{reruns + 1})")

        # Also print to stderr for real-time monitoring
        print(f"\n{retry_msg}\n", file=sys.stderr, flush=True)

        tr = item.config.pluginmanager.getplugin("terminalreporter")
        if tr:
            tr.write_line(retry_msg)

        if delay:
            time.sleep(delay)

    # Emit the final set of reports ourselves and stop further processing
    for rep in last_reports or ():
        item.ihook.pytest_runtest_logreport(report=rep)
    return True  # tell pytest we've handled the protocol
