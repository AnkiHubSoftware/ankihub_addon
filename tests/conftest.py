import json
import pathlib
import shutil

import pytest
from pytest_anki import AnkiSession

ROOT = pathlib.Path(__file__).absolute().parent.parent


@pytest.fixture(scope="function")
def anki_session_with_addon(anki_session: AnkiSession, requests_mock) -> AnkiSession:
    # the requests_mock argument is here to disallow real requests for all tests that use the fixture
    # to prevent hidden real requests

    dest = pathlib.Path(anki_session.mw.addonManager.addonsFolder())
    shutil.copytree(ROOT / "ankihub", dest / "ankihub")

    # clear database
    from ankihub.constants import DB_PATH

    DB_PATH.unlink(missing_ok=True)

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
