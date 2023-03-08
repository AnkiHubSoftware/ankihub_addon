import pathlib
from unittest.mock import MagicMock

from pytest import MonkeyPatch, fixture

from ..fixtures import (  # noqa F401
    disable_image_support_feature_flag,
    enable_image_support_feature_flag,
    next_deterministic_id,
    next_deterministic_uuid,
)

ROOT = pathlib.Path(__file__).parent.parent.parent


@fixture(autouse=True)
def mw_mock(monkeypatch: MonkeyPatch):
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
    mock.addonManager.addonsFolder.return_value = (ROOT / "ankihub").absolute()
    yield mock
