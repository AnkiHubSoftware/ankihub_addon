"""Feature flags are used to enable/disable features on the client side. The flags are fetched from the server."""
from dataclasses import dataclass, fields

from . import LOGGER
from .addon_ankihub_client import AnkiHubClient
from .ankihub_client import AnkiHubRequestException


@dataclass
class _FeatureFlags:
    use_deck_media: bool = False


feature_flags = _FeatureFlags()


def setup_feature_flags() -> None:
    """Fetch feature flags from the server. If the server is not reachable, use the default values."""
    for field in fields(_FeatureFlags):
        _init_feature_flag(field.name, field.default)  # type: ignore
    LOGGER.info(f"Feature flags: {feature_flags}")


def _init_feature_flag(name: str, default: bool) -> None:
    try:
        value = AnkiHubClient().is_feature_flag_enabled(name)
        setattr(feature_flags, name, value)
    except AnkiHubRequestException as e:
        setattr(feature_flags, name, default)
        LOGGER.warning(
            f"Failed to fetch {name} feature flag: {e}, using default value: {default}"
        )
