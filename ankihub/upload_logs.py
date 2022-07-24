import time

from addon_ankihub_client import AnkiHubClient

from . import LOGGER
from .config import config
from .settings import LOG_FILE


def upload_logs() -> None:
    client = AnkiHubClient()
    response = client.upload_logs(
        file=LOG_FILE,
        key=f"ankihub_addon_logs_{config.private_config['user']}_{time.time()}.log",
    )
    if response.status_code != 200:
        LOGGER.debug("Failed to upload logs.")
