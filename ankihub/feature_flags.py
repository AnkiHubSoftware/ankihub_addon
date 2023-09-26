"""Feature flags are used to enable/disable features on the client side. The flags are fetched from the server."""
from dataclasses import dataclass, fields

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException


@dataclass
class _FeatureFlags:
    ...


feature_flags = _FeatureFlags()


def setup_feature_flags() -> None:
    """Fetch feature flags from the server. If the server is not reachable, use the default values."""
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
