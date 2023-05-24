import copy
import os
import uuid
from typing import Callable

from anki.models import NotetypeDict
from aqt.main import AnkiQt
from pytest import fixture
from pytest_anki import AnkiSession
from ankihub.ankihub_client.ankihub_client import DEFAULT_API_URL
from requests_mock import Mocker

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.utils import modify_note_type


@fixture
def next_deterministic_uuid() -> Callable[[], uuid.UUID]:
    """Returns a function that returns a new uuid.UUID each time it is called.
    The uuids are deterministic and are based on the number of times the function has been called.
    """
    counter = 0

    def _next_deterministic_uuid() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(int=counter)

    return _next_deterministic_uuid


@fixture
def next_deterministic_id() -> Callable[[], int]:
    """Returns a function that returns a new int each time it is called.
    The ints are deterministic and are based on the number of times the function has been called.
    """
    counter = 0

    def _next_deterministic_id() -> int:
        nonlocal counter
        counter += 1
        return counter

    return _next_deterministic_id


@fixture
def ankihub_basic_note_type(anki_session_with_addon_data: AnkiSession) -> NotetypeDict:
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw
        result = create_or_get_ah_version_of_note_type(
            mw, mw.col.models.by_name("Basic")
        )
        return result


def create_or_get_ah_version_of_note_type(
    mw: AnkiQt, note_type: NotetypeDict
) -> NotetypeDict:
    note_type = copy.deepcopy(note_type)
    note_type["id"] = 0
    note_type["name"] = note_type["name"] + " (AnkiHub)"

    if model := mw.col.models.by_name(note_type["name"]):
        return model

    modify_note_type(note_type)
    mw.col.models.add_dict(note_type)
    return mw.col.models.by_name(note_type["name"])

@fixture
def set_feature_flag_state(requests_mock: Mocker):
    def set_feature_flag_state_inner(feature_flag_name, is_active=True):
        requests_mock.get(
            f"{DEFAULT_API_URL}/feature-flags",
            status_code=200,
            json={"flags": {feature_flag_name: {"is_active": is_active}}}
        )

    return set_feature_flag_state_inner
