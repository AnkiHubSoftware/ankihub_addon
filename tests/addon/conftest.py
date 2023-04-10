import json
import shutil
import uuid
from pathlib import Path
from typing import Generator

import pytest
from pytest import MonkeyPatch
from pytest_anki import AnkiSession
from requests_mock import Mocker

from ..fixtures import (  # noqa F401
    ankihub_basic_note_type,
    disable_image_support_feature_flag,
    enable_image_support_feature_flag,
    next_deterministic_id,
    next_deterministic_uuid,
)

REPO_ROOT_PATH = Path(__file__).absolute().parent.parent.parent

# id of the Anki profile used for testing
# it is used as the name of the profile data folder in the user_files folder
TEST_PROFILE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# autouse=True is set so that the tests don't fail because without it mw is None
# when imported from aqt (from aqt import mw)
# and some add-ons file use mw when you import them
# this is a workaround for that
# it might be good to change the add-ons file to not do that instead of using autouse=True
# the requests_mock argument is here to disallow real requests for all tests that use the fixture
# to prevent hidden real requests
@pytest.fixture(scope="function", autouse=True)
def anki_session_with_addon_data(
    anki_session: AnkiSession, requests_mock: Mocker, monkeypatch: MonkeyPatch
) -> Generator[AnkiSession, None, None]:
    """Sets up a temporary anki_base folder with the ankihub add-ons data (config, contents of profile_data_folder)
    in it's add-on folder. This is a replacement for running the whole initialization process of the add-on
    which is not needed for most tests (test can call entrypoint.run manually if they need to).
    It also makes it so that the profile_data_folder always has the same name (TEST_PROFILE_ID) to make this
    aspect of the tests deterministic.
    The add-ons code is not copied into the add-on's folder in the temporary anki_base folder.
    Instead the tests run the code in the ankihub folder of the repo.
    """
    from ankihub.entry_point import profile_setup
    from ankihub.settings import config, setup_logger

    config_path = REPO_ROOT_PATH / "ankihub" / "config.json"
    with open(config_path) as f:
        config_dict = json.load(f)
    anki_session.create_addon_config(package_name="ankihub", default_config=config_dict)

    config.setup_public_config_and_urls()
    setup_logger()

    with monkeypatch.context() as m:
        # monkeypatch the uuid4 function to always return the same value so
        # the profile data folder is always the same
        m.setattr("uuid.uuid4", lambda: TEST_PROFILE_ID)
        with anki_session.profile_loaded():
            profile_setup()

    yield anki_session


@pytest.fixture
def anki_session_with_addon_before_profile_support(anki_session_with_addon_data):
    # previous versions of the add-on didn't support multiple Anki profiles and
    # had one set of data for all profiles
    # this fixtures simulates the data structure of such an add-on version
    anki_session: AnkiSession = anki_session_with_addon_data
    with anki_session.profile_loaded():
        mw = anki_session.mw
        user_files_path = Path(mw.addonManager.addonsFolder("ankihub")) / "user_files"

        shutil.rmtree(user_files_path)
        shutil.copytree(
            REPO_ROOT_PATH / "tests" / "addon" / "profile_migration_test_data",
            user_files_path,
        )

    yield anki_session
