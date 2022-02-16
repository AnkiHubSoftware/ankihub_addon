import json
import pathlib
import shutil

import pytest
from pytest_anki import AnkiSession

ROOT = pathlib.Path(__file__).absolute().parent


@pytest.fixture(scope="function")
def anki_session_with_addon(anki_session: AnkiSession):
    dest = pathlib.Path(anki_session.mw.addonManager.addonsFolder())
    shutil.copytree(ROOT, dest / "ankihub")
    yield anki_session


@pytest.fixture(scope="function")
def anki_session_with_config(anki_session: AnkiSession):
    config = ROOT / "config.json"
    meta = ROOT / "meta.json"
    with open(config) as f:
        config_dict = json.load(f)
        config_dict["user"]["token"] = "token"
    with open(meta) as f:
        meta_dict = json.load(f)
    anki_session.create_addon_config(
        package_name="ankihub", default_config=config_dict, user_config=meta_dict
    )
    yield anki_session
