"""Feature flags and user details are fetched from the server to enable/disable features and check user access.

Feature flags control client-side feature availability.
User details include feature access flags (e.g., has_flashcard_selector_access) for gating premium features.
Both are cached locally and refreshed periodically for offline support.
"""

from typing import Callable, List, Optional

import aqt
from aqt import QTimer

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from .gui.operations import AddonQueryOp
from .settings import config

# List of callbacks that are called when the feature flags are updated.
# This can e.g. be used to update the UI once the feature flags are fetched.
_feature_flags_update_callbacks: List[Callable[[], None]] = []

# Timer for periodic refresh
_periodic_refresh_timer: Optional[QTimer] = None


def update_feature_flags_and_user_details_in_background(on_done: Optional[Callable[[], None]] = None) -> None:
    """Fetch feature flags and user details from the server in the background.

    This updates both feature flags (for feature gating) and user details (for access checks).
    Cached values are preserved if the server is unreachable.

    Args:
        on_done: Optional callback to run after the fetch completes
    """
    def _on_done(_: None) -> None:
        LOGGER.info("Fetched feature flags and user details.")

        for callback in _feature_flags_update_callbacks:
            aqt.mw.taskman.run_on_main(callback)

        if on_done:
            aqt.mw.taskman.run_on_main(on_done)

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _fetch_feature_flags_and_user_details(),
        success=_on_done,
    ).without_collection().run_in_background()


def _fetch_feature_flags_and_user_details() -> None:
    """Fetch feature flags and user details from the server. If the server is not reachable, use cached values."""
    client = AnkiHubClient()

    # Fetch feature flags
    try:
        feature_flags_dict = client.get_feature_flags()
        config.set_feature_flags(feature_flags_dict)
        LOGGER.info("Feature flags fetched from server", feature_flags=feature_flags_dict)
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning(f"Failed to fetch feature flags: {exc}. Using cached values.", feature_flags=config.get_feature_flags())
        # Keep the existing cached values - do not overwrite with empty dict

    # Fetch user details (for offline support in deck browser context menu)
    try:
        user_details = client.get_user_details()
        config.set_user_details(user_details)
        LOGGER.info("User details fetched from server", user_id=user_details.get("id"))
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning(f"Failed to fetch user details: {exc}. Using cached values.", user_details=config.get_user_details())
        # Keep the existing cached values - do not overwrite with empty dict


def add_feature_flags_update_callback(callback: Callable[[], None]) -> None:
    """Add a callback to be called when feature flags are updated.

    Args:
        callback: Function to call after feature flags are fetched
    """
    _feature_flags_update_callbacks.append(callback)


def setup_periodic_refresh(interval_minutes: int = 60) -> None:
    """Set up periodic refresh of feature flags and user details during long Anki sessions.

    This allows feature-flagged UI elements to appear when flags are enabled on the server,
    and keeps user details fresh for offline access checks. Since all UI elements auto-refresh via
    hooks (overview_did_refresh, reviewer_did_show_question, etc.), no teardown logic is needed
    when flags are disabled.

    Args:
        interval_minutes: How often to refresh from the server (default: 60 minutes)
    """
    global _periodic_refresh_timer

    # Don't set up multiple timers
    if _periodic_refresh_timer is not None:
        return

    def refresh_and_reschedule() -> None:
        """Refresh remote config and schedule the next refresh."""
        LOGGER.debug(f"Periodic refresh triggered")

        def schedule_next() -> None:
            """Schedule the next timer after this refresh completes."""
            _periodic_refresh_timer.start(interval_minutes * 60 * 1000)
            LOGGER.debug(f"Next refresh scheduled in {interval_minutes} minutes")

        update_feature_flags_and_user_details_in_background(on_done=schedule_next)

    # Create a single-shot timer that triggers the first refresh and reschedules itself
    _periodic_refresh_timer = QTimer()
    _periodic_refresh_timer.setSingleShot(True)
    _periodic_refresh_timer.timeout.connect(refresh_and_reschedule)
    _periodic_refresh_timer.start(interval_minutes * 60 * 1000)  # Convert minutes to milliseconds

    LOGGER.info(f"Set up periodic refresh every {interval_minutes} minutes")
