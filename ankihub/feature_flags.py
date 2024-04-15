"""Feature flags are used to enable/disable features on the client side. The flags are fetched from the server."""

from dataclasses import dataclass, fields
from typing import Callable, List

import aqt

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from .gui.operations import AddonQueryOp


@dataclass
class _FeatureFlags:
    show_flashcards_selector_button: bool = False


feature_flags = _FeatureFlags()

# List of callbacks that are called when the feature flags are updated.
# This can e.g. be used to update the UI once the feature flags are fetched.
_feature_flags_update_callbacks: List[Callable[[], None]] = []


def setup_feature_flags_in_background() -> None:
    def on_done(_: None) -> None:
        LOGGER.info("Set up feature flags.")

        for callback in _feature_flags_update_callbacks:
            callback()

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _setup_feature_flags(),
        success=on_done,
    ).without_collection().run_in_background()


def _setup_feature_flags() -> None:
    """Fetch feature flags from the server. If the server is not reachable, use the default values."""

    if not fields(_FeatureFlags):
        return
    try:
        feature_flags_dict = AnkiHubClient().get_feature_flags()
    except (AnkiHubRequestException, AnkiHubHTTPError) as e:
        LOGGER.warning(
            f"Failed to fetch feature flags from the server: {e}, using default values."
        )
        feature_flags_dict = {}

    # Set the feature flags to the values fetched from the server or to the default values
    for field in fields(_FeatureFlags):
        try:
            value = feature_flags_dict[field.name]
        except KeyError:
            setattr(feature_flags, field.name, field.default)
            LOGGER.warning(
                f"No feature flag named {field.name} found, using default value: {field.default}."
            )
        else:
            setattr(feature_flags, field.name, value)

    LOGGER.info(f"Feature flags: {feature_flags}")


def add_feature_flags_update_callback(callback: Callable[[], None]) -> None:
    _feature_flags_update_callbacks.append(callback)
