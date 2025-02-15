"""Feature flags are used to enable/disable features on the client side. The flags are fetched from the server."""

from typing import Callable, List

import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from .gui.operations import AddonQueryOp
from .settings import config

# List of callbacks that are called when the feature flags are updated.
# This can e.g. be used to update the UI once the feature flags are fetched.
_feature_flags_update_callbacks: List[Callable[[], None]] = []


def update_feature_flags_in_background() -> None:
    def on_done(_: None) -> None:
        LOGGER.info("Set up feature flags.")

        for callback in _feature_flags_update_callbacks:
            aqt.mw.taskman.run_on_main(callback)

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _setup_feature_flags(),
        success=on_done,
    ).without_collection().run_in_background()


def _setup_feature_flags() -> None:
    """Fetch feature flags from the server. If the server is not reachable, use the default values."""
    feature_flags_dict = {}
    try:
        feature_flags_dict = AnkiHubClient().get_feature_flags()
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.error(f"Failed to fetch feature flags: {exc}. Using default values.")
    else:
        config.set_feature_flags(feature_flags_dict)

    config.set_feature_flags(feature_flags_dict)
    LOGGER.info("Feature flags", feature_flags=feature_flags_dict)


def add_feature_flags_update_callback(callback: Callable[[], None]) -> None:
    _feature_flags_update_callbacks.append(callback)
