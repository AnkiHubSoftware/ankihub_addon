import dataclasses
import json
import logging
import os
import sys
from datetime import datetime
from enum import Enum
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pprint import pformat
from typing import Any, Callable, Dict, Optional

from anki.buildinfo import version as ANKI_VERSION
from aqt import mw

from . import LOGGER, ankihub_client


@dataclasses.dataclass
class PrivateConfig:
    token: str = ""
    user: str = ""
    decks: Dict[int, Dict[str, Any]] = dataclasses.field(default_factory=dict)


class Config:
    def __init__(self):
        # self.public_config is editable by the user.
        self.public_config: Dict[str, Any] = mw.addonManager.getConfig(__name__)
        # This is the location for private config which is only managed by our code
        # and is not exposed to the user.
        # See https://addon-docs.ankiweb.net/addon-config.html#user-files
        addon_dir_name = mw.addonManager.addonFromModule(__name__)
        user_files_path = os.path.join(
            mw.addonManager.addonsFolder(addon_dir_name), "user_files"
        )
        self._private_config_file_path = os.path.join(
            user_files_path, ".private_config.json"
        )
        if not os.path.exists(self._private_config_file_path):
            self.private_config = self.new_config()
        else:
            with open(self._private_config_file_path) as f:
                try:
                    self.private_config = PrivateConfig(**json.load(f))
                except JSONDecodeError:
                    # TODO Instead of overwriting, query AnkiHub for config values.
                    self.private_config = self.new_config()
        self._log_private_config()
        self.token_change_hook: Optional[Callable[[], None]] = None
        self.subscriptions_change_hook: Optional[Callable[[], None]] = None

    def new_config(self):
        private_config = PrivateConfig()
        config_dict = dataclasses.asdict(private_config)
        with open(self._private_config_file_path, "w") as f:
            f.write(json.dumps(config_dict, indent=4, sort_keys=True))
        return private_config

    def _update_private_config(self):
        with open(self._private_config_file_path, "w") as f:
            config_dict = dataclasses.asdict(self.private_config)
            f.write(json.dumps(config_dict, indent=4, sort_keys=True))
        self._log_private_config()

    def save_token(self, token: str):
        self.private_config.token = token
        self._update_private_config()
        if self.token_change_hook:
            self.token_change_hook()

    def save_user_email(self, user_email: str):
        self.private_config.user = user_email
        self._update_private_config()

    def save_latest_update(self, ankihub_did: str, time: Optional[str]):
        if time is None:
            self.private_config.decks[ankihub_did]["latest_update"] = None
        else:
            date_object = datetime.strptime(time, "%Y-%m-%dT%H:%M:%S.%f%z")
            date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")
            self.private_config.decks[ankihub_did]["latest_update"] = date_time_str
        self._update_private_config()

    def save_subscription(
        self,
        name: str,
        ankihub_did: str,
        anki_did: int,
        creator: bool = False,
        last_update: Optional[str] = None,
    ) -> None:
        self.private_config.decks[ankihub_did] = {
            "name": name,
            "anki_id": anki_did,
            "creator": creator,
        }
        # remove duplicates
        self.save_latest_update(ankihub_did, last_update)
        self._update_private_config()

        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def unsubscribe_deck(self, ankihub_did: str) -> None:
        self.private_config.decks.pop(ankihub_did)
        self._update_private_config()

        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def _log_private_config(self):
        config_dict = dataclasses.asdict(self.private_config)
        if config_dict["token"]:
            config_dict["token"] = "REDACTED"
        LOGGER.debug(f"private config:\n{pformat(config_dict)}")


config: Config = Config()

LOG_FILE = Path(__file__).parent / "user_files/ankihub.log"


def stdout_handler():
    return logging.StreamHandler(stream=sys.stdout)


def file_handler():
    return RotatingFileHandler(
        LOG_FILE, maxBytes=3000000, backupCount=5, encoding="utf-8"
    )


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(levelname)s %(asctime)s %(module)s "
            "%(process)d %(thread)d %(message)s"
        }
    },
    "handlers": {
        "console": {
            "()": stdout_handler,
            "level": "DEBUG",
            "formatter": "verbose",
        },
        "file": {
            "()": file_handler,
            "level": "DEBUG",
            "formatter": "verbose",
        },
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["console", "file"],
    },
}

logging.config.dictConfig(LOGGING)


version_file = Path(__file__).parent / "VERSION"
with version_file.open() as f:
    ADDON_VERSION: str = f.read().strip()
LOGGER.debug(f"version: {ADDON_VERSION}")
LOGGER.debug(f"VERSION file: {version_file}")

try:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    ANKIWEB_ID = manifest.get("ankiweb_id")
except (FileNotFoundError, KeyError):
    ANKIWEB_ID = 1322529746


ANKIHUB_APP_URL = os.getenv("ANKIHUB_APP_URL")
if ANKIHUB_APP_URL is None:
    ANKIHUB_APP_URL = config.public_config.get("ankihub_url")
    ANKIHUB_APP_URL = ANKIHUB_APP_URL if ANKIHUB_APP_URL else "https://app.ankihub.net"
API_URL_BASE = f"{ANKIHUB_APP_URL}/api"
LOGGER.debug(f"Starting with URL_BASE {API_URL_BASE}")

# maybe override default API_URL_BASE of client
ankihub_client.API_URL_BASE = API_URL_BASE

URL_VIEW_NOTE = f"{ANKIHUB_APP_URL}/decks/notes/"
URL_VIEW_DECK = f"{ANKIHUB_APP_URL}/decks/"
URL_HELP = f"{ANKIHUB_APP_URL}/help"
URL_DECKS = f"{ANKIHUB_APP_URL}/explore"
URL_DECK_BASE = f"{ANKIHUB_APP_URL}/decks"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "ankihub_id"
ANKIHUB_NOTE_TYPE_MODIFICATION_STRING = "ANKIHUB MODFICATIONS"
ADDON_PATH = Path(__file__).parent.absolute()
ADDON_PACKAGE = __name__.split(".")[0]
ICONS_PATH = ADDON_PATH / "icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"

ANKI_MINOR = int(ANKI_VERSION.split(".")[2])

USER_FILES_PATH = Path(__file__).parent / "user_files"
DB_PATH = USER_FILES_PATH / "ankihub.db"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"


RATIONALE_FOR_CHANGE_MAX_LENGTH = 1024
