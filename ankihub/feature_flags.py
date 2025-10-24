"""Feature flags are used to enable/disable features on the client side. The flags are fetched from the server."""

from typing import Callable, List, Optional

import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from .gui.operations import AddonQueryOp
from .settings import config

# List of callbacks that are called when the feature flags are updated.
# This can e.g. be used to update the UI once the feature flags are fetched.
_feature_flags_update_callbacks: List[Callable[[], None]] = []


def update_feature_flags_in_background(on_done: Optional[Callable[[], None]] = None) -> None:
    def _on_done(_: None) -> None:
        LOGGER.info("Set up feature flags.")

        for callback in _feature_flags_update_callbacks:
            aqt.mw.taskman.run_on_main(callback)

        if on_done:
            aqt.mw.taskman.run_on_main(on_done)

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _setup_feature_flags(),
        success=_on_done,
    ).without_collection().run_in_background()


def _setup_feature_flags() -> None:
    """Fetch feature flags from the server. If the server is not reachable, use cached values."""
    try:
        feature_flags_dict = AnkiHubClient().get_feature_flags()
        config.set_feature_flags(feature_flags_dict)
        LOGGER.info("Feature flags fetched from server", feature_flags=feature_flags_dict)
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning(f"Failed to fetch feature flags: {exc}. Using cached values.", feature_flags=config.get_feature_flags())
        # Keep the existing cached values - do not overwrite with empty dict


def add_feature_flags_update_callback(callback: Callable[[], None]) -> None:
    _feature_flags_update_callbacks.append(callback)
