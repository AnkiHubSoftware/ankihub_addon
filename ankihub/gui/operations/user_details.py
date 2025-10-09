from typing import Callable, Optional

import aqt

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from ...gui.operations import AddonQueryOp
from ...settings import config


def _fetch_user_details() -> None:
    if not config.is_logged_in():
        config.save_username("")
        config.save_user_id(None)
        return

    if config.username() and config.user_id():
        return

    username = ""
    user_id = None
    client = AnkiHubClient()
    try:
        user_details = client.get_user_details()
        username = user_details["username"]
        user_id = user_details["id"]
    except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
        LOGGER.warning(f"Failed to fetch user details: {exc}")
    config.save_username(username)
    config.save_user_id(user_id)


def fetch_user_details_in_background() -> None:
    def on_done(_: None) -> None:
        LOGGER.info("Fetched user details.")

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _fetch_user_details(),
        success=on_done,
    ).without_collection().run_in_background()


def check_user_feature_access(
    feature_key: str,
    on_access_granted: Callable[[dict], None],
    on_access_denied: Optional[Callable[[dict], None]] = None,
    on_failure: Optional[Callable[[Exception], None]] = None,
    parent=None,
) -> None:
    """
    Fetches user details and executes callbacks based on feature access.

    Args:
        feature_key: The key in user_details to check (e.g., "has_flashcard_selector_access")
        on_access_granted: Callback to call if user has access, receives user_details dict
        on_access_denied: Optional callback to call if user doesn't have access, receives user_details dict
        on_failure: Optional callback to call if fetching user details fails, receives exception
        parent: Parent widget for the operation (defaults to aqt.mw)
    """

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
