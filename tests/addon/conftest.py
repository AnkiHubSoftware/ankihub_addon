import json
import pathlib
import shutil

import pytest
from pytest_anki import AnkiSession

ROOT = pathlib.Path(__file__).absolute().parent.parent.parent


@pytest.fixture(scope="function")
def anki_session_with_addon(
    anki_session: AnkiSession, requests_mock, monkeypatch
) -> AnkiSession:
    """Sets up the add-on, config and database and returns the AnkiSession.
    Does similar setup like in profile_setup in entry_point.py.
    """
    # the requests_mock argument is here to disallow real requests for all tests that use the fixture
    # to prevent hidden real requests

    ANKIHUB_PATH = pathlib.Path(anki_session.mw.addonManager.addonsFolder()) / "ankihub"
    USER_FILES_PATH = ANKIHUB_PATH / "user_files"
    PROFILE_DATA_PATH = USER_FILES_PATH / "test_profile_folder"

    # copy the addon to the addons folder
    shutil.copytree(ROOT / "ankihub", ANKIHUB_PATH)

    # clear ther user files folder at the destination - it might contain files from using the add-on
    for f in USER_FILES_PATH.glob("*"):
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    # create the profile data folder
    PROFILE_DATA_PATH.mkdir(parents=True)

    from ankihub.settings import config
    from ankihub.db import ankihub_db

    # monkeypatch the paths to the user files and the profile data folder
    monkeypatch.setattr(
        "ankihub.settings.user_files_path",
        lambda: USER_FILES_PATH,
    )
    monkeypatch.setattr(
        "ankihub.settings.profile_files_path",
        lambda: PROFILE_DATA_PATH,
    )

    # setup the config
    config.setup()

    # setup the database
    ankihub_db.setup_and_migrate()

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
