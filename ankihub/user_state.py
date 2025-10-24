"""Manage user feature access state by fetching feature flags and user details from the server.

This module refreshes the user's feature access state, which is determined by:
- Feature flags: Which features are enabled on the server
- User details: Which features this specific user has access to (e.g., has_flashcard_selector_access)

Both are cached locally and refreshed periodically to keep feature-gated UI elements current.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

import aqt
from aqt import QTimer

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from .gui.operations import AddonQueryOp
from .settings import config


@dataclass
class _PeriodicRefreshState:
    """Module state managing a periodic refresh timer and feature flag callbacks."""

    timer: Optional[QTimer] = None
    callbacks: List[Callable[[], None]] = field(default_factory=list)


_state = _PeriodicRefreshState()


def refresh_user_state_in_background(on_done: Optional[Callable[[], None]] = None) -> None:
    """Refresh user state (feature flags and user details) from the server in the background.

    This fetches both feature flags (to know which features are enabled on the server)
    and user details (to know which features this user has access to).
    Cached values are preserved if the server is unreachable.

    Args:
        on_done: Optional callback to run after the fetch completes
    """

    def _on_done(_: None) -> None:
        LOGGER.info("feature_flags_and_user_details_fetched")

        for callback in _state.callbacks:
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
        LOGGER.info("feature_flags_fetched_from_server")
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning("failed_to_fetch_feature_flags", exception=str(exc))
        # Keep the existing cached values

    # Fetch user details (for offline support)
    try:
        user_details = client.get_user_details()
        config.set_user_details(user_details)
        LOGGER.info("user_details_fetched_from_server")
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning("failed_to_fetch_user_details", exception=str(exc))
        # Keep the existing cached values


def add_feature_flags_update_callback(callback: Callable[[], None]) -> None:
    """Add a callback to be called when feature flags are updated.

    Args:
        callback: Function to call after feature flags are fetched
    """
    _state.callbacks.append(callback)


def setup_periodic_user_state_refresh(interval_minutes: int = 60) -> None:
    """Set up periodic refresh of user state during long Anki sessions.

    This refreshes both feature flags (server-side feature availability) and user details
    (user's access to features) so feature-gated UI elements are kept up-to-date.

    Args:
        interval_minutes: How often to refresh from the server (default: 60 minutes)
    """
    # Don't set up multiple timers
    if _state.timer is not None:
        return

    _state.timer = QTimer()
    _state.timer.timeout.connect(lambda: refresh_user_state_in_background())
    _state.timer.start(interval_minutes * 60 * 1000)  # Convert minutes to milliseconds

    LOGGER.info("periodic_refresh_setup", interval_minutes=interval_minutes)


def check_user_feature_access(
    feature_key: str,
    on_access_granted: Callable[[dict], None],
    on_access_denied: Optional[Callable[[dict], None]] = None,
    on_failure: Optional[Callable[[Exception], None]] = None,
    parent=None,
    use_cached: bool = False,
) -> None:
    """Check user feature access, optionally using cached values for offline support.

    Args:
        feature_key: The key in user_details to check (e.g., "has_flashcard_selector_access")
        on_access_granted: Callback to call if user has access, receives user_details dict
        on_access_denied: Optional callback to call if user doesn't have access, receives user_details dict
        on_failure: Optional callback to call if fetching user details fails, receives exception
        parent: Parent widget for the operation (defaults to aqt.mw)
        use_cached: If True, use cached user details (for offline support).
                    If False, fetch fresh user details from server (default behavior).
    """

    # Use cached values if requested (for offline support)
    if use_cached:
        cached_details = config.get_user_details()
        if cached_details:
            if cached_details.get(feature_key):
                on_access_granted(cached_details)
            elif on_access_denied:
                on_access_denied(cached_details)
            return
        # If no cache available, fall through to fresh fetch

    # Fetch fresh user details from server
    def on_fetched_user_details(user_details: dict) -> None:
        if user_details.get(feature_key):
            on_access_granted(user_details)
        elif on_access_denied:
            on_access_denied(user_details)

    op = AddonQueryOp(
        op=lambda _: AnkiHubClient().get_user_details(),
        success=on_fetched_user_details,
        parent=parent or aqt.mw,
    ).without_collection()

    if on_failure:
        op = op.failure(on_failure)

    op.run_in_background()
