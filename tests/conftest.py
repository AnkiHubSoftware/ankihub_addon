import json
import pathlib
import shutil
from unittest.mock import MagicMock

import pytest
from pytest_anki import AnkiSession

ROOT = pathlib.Path(__file__).absolute().parent.parent


@pytest.fixture(scope="function")
def anki_session_with_addon(anki_session: AnkiSession, requests_mock) -> AnkiSession:
    # the requests_mock argument is here to disallow real requests for all tests that use the fixture
    # to prevent hidden real requests

    dest = pathlib.Path(anki_session.mw.addonManager.addonsFolder())
    shutil.copytree(ROOT / "ankihub", dest / "ankihub")

    # clear user files
    from ankihub.constants import USER_FILES_PATH

    for f in USER_FILES_PATH.glob("*"):
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    yield anki_session


@pytest.fixture(scope="function")
def anki_session_with_config(anki_session: AnkiSession):
    config = ROOT / "ankihub" / "config.json"
    meta = ROOT / "ankihub" / "meta.json"
    with open(config) as f:
        config_dict = json.load(f)
    with open(meta) as f:
        meta_dict = json.load(f)
    anki_session.create_addon_config(
        package_name="ankihub", default_config=config_dict, user_config=meta_dict
    )
    yield anki_session


@pytest.fixture
def mw_mock(monkeypatch):
    """Mock the AnkiQt object."""

    import aqt

    mock = MagicMock()
    monkeypatch.setattr(aqt, "mw", mock)

    # Mock methods called by our Config object.
    mock.addonManager.getConfig.return_value = {
        "ankihub_url": "https://app.ankihub.net",
        "hotkey": "Alt+u",
        "report_errors": True,
        "sync_on_startup": True,
    }
    mock.addonManager.addonFromModule.return_value = "ankihub"
    mock.addonManager.addonsFolder.return_value = (
        pathlib.Path(__file__).parent.parent / "ankihub"
    ).absolute()
    yield mock
