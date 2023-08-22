import copy
import os
import uuid
from dataclasses import fields
from typing import Any, Callable, Optional, Protocol
from unittest.mock import Mock

import pytest
from anki.models import NotetypeDict
from aqt.main import AnkiQt
from pytest import MonkeyPatch, fixture
from pytest_anki import AnkiSession

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client.ankihub_client import AnkiHubClient
from ankihub.feature_flags import _FeatureFlags, setup_feature_flags
from ankihub.main.utils import modify_note_type


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


class SetFeatureFlagState(Protocol):
    def __call__(self, feature_flag_name: str, is_active: bool = True) -> None:
        ...


@fixture
def set_feature_flag_state(monkeypatch: MonkeyPatch) -> SetFeatureFlagState:
    """Patches the AnkiHubClient.is_feature_flag_enabled method to return the desired value for
    the provided feature flag and reloads feature flags."""

    def set_feature_flag_state_inner(feature_flag_name, is_active=True) -> None:

        # Patch the AnkiHubClient.is_feature_flag_enabled method to return the desired value
        # for the provided feature flag.
        old_is_feature_flag_enabled = AnkiHubClient.is_feature_flag_enabled

        def new_is_feature_flag_enabled(self, flag_name: str) -> bool:
            if flag_name == feature_flag_name:
                return is_active
            return old_is_feature_flag_enabled(self, flag_name)

        monkeypatch.setattr(
            "ankihub.ankihub_client.ankihub_client.AnkiHubClient.is_feature_flag_enabled",
            new_is_feature_flag_enabled,
        )

        # this is needed so that the feature flags are reloaded for the feature_flags singleton
        setup_feature_flags()

    return set_feature_flag_state_inner


class MockFunction(Protocol):
    def __call__(
        self,
        target_object: Any,
        target_function_name: str,
        return_value: Optional[Any] = None,
        side_effect: Optional[Callable] = None,
    ) -> Mock:
        ...


@pytest.fixture
def mock_function(
    monkeypatch: MonkeyPatch,
) -> MockFunction:
    def _mock_function(
        target_object: Any,
        target_function_name: str,
        return_value: Optional[Any] = None,
        side_effect: Optional[Callable] = None,
    ) -> Mock:
        mock = Mock()
        mock.return_value = return_value
        monkeypatch.setattr(
            target_object,
            target_function_name,
            mock,
        )
        mock.side_effect = side_effect
        return mock

    return _mock_function


@pytest.fixture
def mock_all_feature_flags_to_default_values(set_feature_flag_state):
    for field in fields(_FeatureFlags):
        set_feature_flag_state(field.name, field.default)
