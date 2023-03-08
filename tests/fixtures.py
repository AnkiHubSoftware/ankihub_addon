import uuid
from typing import Callable

import pytest
from pytest import fixture
from requests_mock import Mocker


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


@pytest.fixture
def enable_image_support_feature_flag(requests_mock: Mocker) -> None:
    from ankihub.ankihub_client import API_URL_BASE

    requests_mock.get(
        f"{API_URL_BASE}/feature-flags",
        status_code=200,
        json={"flags": {"image_support_enabled": {"is_active": True}}},
    )


@pytest.fixture
def disable_image_support_feature_flag(requests_mock: Mocker) -> None:
    from ankihub.ankihub_client import API_URL_BASE

    requests_mock.get(
        f"{API_URL_BASE}/feature-flags",
        status_code=200,
        json={"flags": {"image_support_enabled": {"is_active": False}}},
    )
