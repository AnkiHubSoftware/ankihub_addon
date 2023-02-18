import json
import shutil
import uuid
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from pytest_anki import AnkiSession
from requests_mock import Mocker

REPO_ROOT_PATH = Path(__file__).absolute().parent.parent.parent

# id of the Anki profile used for testing
# it is used as the name of the profile data folder in the user_files folder
TEST_PROFILE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# autouse=True is set so that the tests don't fail because without it mw is None
# when imported from aqt (from aqt import mw)
# and some add-ons file use mw when you import them
# this is a workaround for that
# it might be good to change the add-ons file to not do that instead of using autouse=True
@pytest.fixture(scope="function", autouse=True)
def anki_session_with_addon(
    anki_session: AnkiSession, requests_mock: Mocker, monkeypatch: MonkeyPatch
) -> AnkiSession:
    """Sets up the add-on, config and database and returns the AnkiSession.
    Does similar setup like in profile_setup in entry_point.py.
    """
    # the requests_mock argument is here to disallow real requests for all tests that use the fixture
    # to prevent hidden real requests

    ANKIHUB_PATH = Path(anki_session.mw.addonManager.addonsFolder()) / "ankihub"
    USER_FILES_PATH = ANKIHUB_PATH / "user_files"
    PROFILE_DATA_PATH = USER_FILES_PATH / "test_profile_folder"

    # copy the addon to the addons folder
    shutil.copytree(REPO_ROOT_PATH / "ankihub", ANKIHUB_PATH)

    # clear the user files folder at the destination - it might contain files from using the add-on
    for f in USER_FILES_PATH.glob("*"):
        if f.is_file():
            f.unlink()
        elif f.is_dir():
            shutil.rmtree(f)

    # create the profile data folder
    PROFILE_DATA_PATH.mkdir(parents=True)

    from ankihub.entry_point import profile_setup

    with monkeypatch.context() as m:
        # monkeypatch the uuid4 function to always return the same value so
        # the profile data folder is always the same
        m.setattr("uuid.uuid4", lambda: TEST_PROFILE_ID)
        with anki_session.profile_loaded():
            profile_setup()

    yield anki_session


@pytest.fixture(scope="function")
def anki_session_with_config(anki_session: AnkiSession):
    config = REPO_ROOT_PATH / "ankihub" / "config.json"
    meta = REPO_ROOT_PATH / "ankihub" / "meta.json"
    with open(config) as f:
        config_dict = json.load(f)
    with open(meta) as f:
        meta_dict = json.load(f)
    anki_session.create_addon_config(
        package_name="ankihub", default_config=config_dict, user_config=meta_dict
    )
    yield anki_session


@pytest.fixture
def anki_session_with_addon_before_profile_support(anki_session_with_addon):
    # previous versions of the add-on didn't support multiple Anki profiles and
    # had one set of data for all profiles
    # this fixtures simulates the data structure of such an add-on version
    anki_session: AnkiSession = anki_session_with_addon
    with anki_session.profile_loaded():
        mw = anki_session.mw
        user_files_path = Path(mw.addonManager.addonsFolder("ankihub")) / "user_files"

        shutil.rmtree(user_files_path)
        shutil.copytree(
            REPO_ROOT_PATH / "tests" / "addon" / "profile_migration_test_data",
            user_files_path,
        )

    yield anki_session


@pytest.fixture
def enable_image_support_feature_flag(requests_mock: Mocker):
    from ankihub.ankihub_client import API_URL_BASE

    requests_mock.get(
        f"{API_URL_BASE}/waffle/waffle_status",
        status_code=200,
        json={"flags": {"image_support_enabled": {"is_active": True}}},
    )


@pytest.fixture
def disable_image_support_feature_flag(requests_mock: Mocker):
    from ankihub.ankihub_client import API_URL_BASE

    requests_mock.get(
        f"{API_URL_BASE}/waffle/waffle_status",
        status_code=200,
        json={"flags": {"image_support_enabled": {"is_active": False}}},
    )
