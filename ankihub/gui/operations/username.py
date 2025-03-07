import aqt

from ... import LOGGER
from ...addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ...ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from ...gui.operations import AddonQueryOp
from ...settings import config


def _fetch_username_in_background() -> None:
    username = ""
    if config.is_logged_in() and not config.username():
        client = AnkiHubClient()
        try:
            username = client.get_user_details()["username"]
        except (AnkiHubRequestException, AnkiHubHTTPError) as exc:
            LOGGER.warning(f"Failed to fetch username: {exc}")
    config.save_username(username)


def fetch_username_in_background() -> None:
    def on_done(_: None) -> None:
        LOGGER.info("Fetched username.")

    AddonQueryOp(
        parent=aqt.mw,
        op=lambda _: _fetch_username_in_background(),
        success=on_done,
    ).without_collection().run_in_background()
