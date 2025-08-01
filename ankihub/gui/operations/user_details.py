import aqt

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from ...gui.operations import AddonQueryOp
from ...settings import config


def _fetch_user_details_in_background() -> None:
    username = ""
    user_id = None
    if config.is_logged_in() and (not config.username() or not config.user_id()):
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
        op=lambda _: _fetch_user_details_in_background(),
        success=on_done,
    ).without_collection().run_in_background()
