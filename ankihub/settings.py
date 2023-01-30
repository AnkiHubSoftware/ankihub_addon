import dataclasses
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from enum import Enum
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pprint import pformat
from shutil import copyfile, rmtree
from typing import Any, Callable, Dict, List, Optional

from anki.buildinfo import version as ANKI_VERSION
from anki.decks import DeckId
from aqt import mw
from aqt.utils import askUser, showInfo

from . import LOGGER, ankihub_client
from .ankihub_client import ANKIHUB_DATETIME_FORMAT_STR

ADDON_PATH = Path(__file__).parent.absolute()

ANKIHUB_DB_FILENAME = "ankihub.db"
PRIVATE_CONFIG_FILENAME = ".private_config.json"

# the id of the Anki profile is saved under this key in Anki's profile config
# (profile configs are stored by Anki in prefs21.db in the anki base directory)
PROFILE_ID_FIELD_NAME = "ankihub_id"


@dataclasses.dataclass
class DeckConfig:
    anki_id: DeckId
    creator: bool
    name: str
    latest_update: Optional[str] = None
    subdecks_enabled: bool = (
        False  # whether deck is organized into subdecks by the add-on
    )


@dataclasses.dataclass
class UIConfig:
    # whether the trees in the browser sidebar are expanded or collapsed
    ankihub_tree_expanded: bool = True
    updated_today_tree_expanded: bool = False


@dataclasses.dataclass
class PrivateConfig:
    token: str = ""
    user: str = ""
    decks: Dict[uuid.UUID, DeckConfig] = dataclasses.field(default_factory=dict)
    ui: UIConfig = dataclasses.field(default_factory=UIConfig)


class Config:
    def __init__(self):
        # self.public_config is editable by the user using a built-in Anki feature.
        self.public_config: Dict[str, Any] = mw.addonManager.getConfig(ADDON_PATH.name)
        self.token_change_hook: Optional[Callable[[], None]] = None
        self.subscriptions_change_hook: Optional[Callable[[], None]] = None

    def setup(self):
        self._private_config_path = private_config_path()
        if not self._private_config_path.exists():
            self._private_config = self.new_config()
        else:
            try:
                self._private_config = self._load_private_config()
            except JSONDecodeError:
                # TODO Instead of overwriting, query AnkiHub for config values.
                self._private_config = self.new_config()
        self._log_private_config()

    def _load_private_config(self) -> PrivateConfig:
        with open(self._private_config_path) as f:
            private_config_dict = json.load(f)

        # convert deck keys from strings to uuid.UUID
        decks_dict = private_config_dict["decks"]
        private_config_dict["decks"] = {
            uuid.UUID(k): DeckConfig(**v) for k, v in decks_dict.items()
        }

        # parse ui config
        if "ui" in private_config_dict:
            private_config_dict["ui"] = UIConfig(**private_config_dict["ui"])
        else:
            # previous versions of the add-on did not have a ui config
            private_config_dict["ui"] = UIConfig()

        result = PrivateConfig(**private_config_dict)
        return result

    def new_config(self):
        private_config = PrivateConfig()
        config_dict = dataclasses.asdict(private_config)
        with open(self._private_config_path, "w") as f:
            f.write(json.dumps(config_dict, indent=4, sort_keys=True))
        return private_config

    def _update_private_config(self):
        with open(self._private_config_path, "w") as f:
            config_dict = dataclasses.asdict(self._private_config)
            # convert uuid keys to strings
            config_dict["decks"] = {str(k): v for k, v in config_dict["decks"].items()}
            f.write(json.dumps(config_dict, indent=4, sort_keys=True))
        self._log_private_config()

    def save_token(self, token: str):
        self._private_config.token = token
        self._update_private_config()
        if self.token_change_hook:
            self.token_change_hook()

    def save_user_email(self, user_email: str):
        self._private_config.user = user_email
        self._update_private_config()

    def save_latest_update(
        self, ankihub_did: uuid.UUID, latest_update: Optional[datetime]
    ):
        if latest_update is None:
            self.deck_config(ankihub_did).latest_update = None
        else:
            date_time_str = datetime.strftime(
                latest_update, ANKIHUB_DATETIME_FORMAT_STR
            )
            self.deck_config(ankihub_did).latest_update = date_time_str
        self._update_private_config()

    def set_subdecks(self, ankihub_did: uuid.UUID, subdecks: bool):
        self.deck_config(ankihub_did).subdecks_enabled = subdecks
        self._update_private_config()

    def save_subscription(
        self,
        name: str,
        ankihub_did: uuid.UUID,
        anki_did: DeckId,
        creator: bool = False,
        latest_udpate: Optional[datetime] = None,
        subdecks_enabled: bool = False,
    ) -> None:
        self._private_config.decks[ankihub_did] = DeckConfig(
            name=name,
            anki_id=DeckId(anki_did),
            creator=creator,
            subdecks_enabled=subdecks_enabled,
        )
        # remove duplicates
        self.save_latest_update(ankihub_did, latest_udpate)
        self._update_private_config()

        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def unsubscribe_deck(self, ankihub_did: uuid.UUID) -> None:
        self._private_config.decks.pop(ankihub_did)
        self._update_private_config()

        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def _log_private_config(self):
        config_dict = dataclasses.asdict(self._private_config)
        if config_dict["token"]:
            config_dict["token"] = "REDACTED"
        LOGGER.debug(f"private config:\n{pformat(config_dict)}")

    def set_home_deck(self, ankihub_did: uuid.UUID, anki_did: DeckId):
        self.deck_config(ankihub_did).anki_id = anki_did
        self._update_private_config()

    def deck_ids(self) -> List[uuid.UUID]:
        return list(self._private_config.decks.keys())

    def deck_config(self, ankihub_did: uuid.UUID) -> DeckConfig:
        return self._private_config.decks[ankihub_did]

    def token(self) -> Optional[str]:
        return self._private_config.token

    def user(self) -> Optional[str]:
        return self._private_config.user

    def ui_config(self) -> UIConfig:
        return self._private_config.ui

    def set_ui_config(self, ui_config: UIConfig):
        self._private_config.ui = ui_config
        self._update_private_config()


config = Config()


def setup_profile_data_folder() -> bool:
    """Returns False if the migration from the old location needs yet to be done."""
    assign_id_to_profile_if_not_exists()
    LOGGER.debug(f"Anki profile id: {mw.pm.profile[PROFILE_ID_FIELD_NAME]}")

    if not (path := profile_files_path()).exists():
        path.mkdir(parents=True)

    if profile_data_exists_at_old_location():
        return migrate_profile_data_from_old_location()

    return True


def assign_id_to_profile_if_not_exists() -> None:
    """Assigns an id to the currently open profile if it doesn't have one."""
    if mw.pm.profile.get("ankihub_id") is not None:
        return

    mw.pm.profile[PROFILE_ID_FIELD_NAME] = str(uuid.uuid4())
    mw.pm.save()

    LOGGER.debug(
        f"Assigned new id to Anki profile: {mw.pm.profile[PROFILE_ID_FIELD_NAME]}"
    )


def user_files_path() -> Path:
    # The contents of the user_files folder are retained during updates.
    # See https://addon-docs.ankiweb.net/addon-config.html#user-files
    addon_dir_name = mw.addonManager.addonFromModule(__name__)
    result = Path(mw.addonManager.addonsFolder(addon_dir_name)) / "user_files"
    return result


def profile_files_path() -> Path:
    """Path to the add-on data for this Anki profile."""
    # we need an id instead of using the profile name because profiles can be renamed
    cur_profile_id = mw.pm.profile[PROFILE_ID_FIELD_NAME]
    result = user_files_path() / cur_profile_id
    return result


def ankihub_db_path() -> Path:
    result = profile_files_path() / ANKIHUB_DB_FILENAME
    return result


def private_config_path() -> Path:
    result = profile_files_path() / PRIVATE_CONFIG_FILENAME
    return result


def profile_data_exists_at_old_location() -> bool:
    result = (user_files_path() / PRIVATE_CONFIG_FILENAME).exists()
    return result


def migrate_profile_data_from_old_location() -> bool:
    """Returns True if the data was migrated and False if it remains at the old location."""
    if not profile_data_exists_at_old_location():
        LOGGER.debug("No data to migrate.")
        return True

    if len(mw.pm.profiles()) > 1:
        if not askUser(
            (
                "The AnkiHub add-on now has support for multiple Anki profiles!<br><br>"
                "Is this the profile that you were using AnkiHub with before?<br>"
            ),
            title="AnkiHub",
        ):
            showInfo(
                "Please switch to the profile that you were using AnkiHub with before (File -> Switch Profile)."
                "The add-on will then again ask you if this is the correct profile.<br><br>"
                "<b>Note that you won't be able to use the AnkiHub add-on until you do this.</b>"
            )
            return False

    # move database, config and log files to profile folder
    try:
        for file in user_files_path().glob("*"):
            if not file_should_be_migrated(file):
                continue

            copyfile(file, profile_files_path() / file.name)
    except Exception as e:
        LOGGER.error(
            f"Failed to migrate profile data from user_files to profile folder: {e}"
        )
        # remove the profile folder to avoid a partial migration
        rmtree(profile_files_path())
        raise e

    # delete old files after all files have been copied successfully
    for file in user_files_path().glob("*"):
        if not file_should_be_migrated(file):
            continue

        file.unlink()

    return True


def file_should_be_migrated(file_path: Path) -> bool:
    result = (
        file_path.is_file()
        and not str(file_path.name).startswith("README")
        and not re.match(r".+\.log(\.\d+)?$", file_path.name)
    )
    return result


def log_file_path() -> Path:
    return user_files_path() / "ankihub.log"


def stdout_handler():
    return logging.StreamHandler(stream=sys.stdout)


def file_handler():
    return RotatingFileHandler(
        log_file_path(), maxBytes=3000000, backupCount=5, encoding="utf-8"
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
URL_VIEW_NOTE_HISTORY = f"{ANKIHUB_APP_URL}/decks/{{ankihub_did}}/suggestions/?search=note:{{ankihub_nid}} state:closed"
URL_VIEW_DECK = f"{ANKIHUB_APP_URL}/decks/"
URL_HELP = f"{ANKIHUB_APP_URL}/help"
URL_DECKS = f"{ANKIHUB_APP_URL}/explore"
URL_DECK_BASE = f"{ANKIHUB_APP_URL}/decks"
ANKIHUB_NOTE_TYPE_FIELD_NAME = "ankihub_id"
ANKIHUB_NOTE_TYPE_MODIFICATION_STRING = "ANKIHUB MODFICATIONS"
ANKIHUB_TEMPLATE_END_COMMENT = (
    "<!--\n"
    "ANKIHUB_END\n"
    "Text below this comment will not be modified by AnkiHub or AnKing add-ons.\n"
    "Do not edit or remove this comment if you want to protect the content below.\n"
    "-->"
)
ADDON_PACKAGE = __name__.split(".")[0]
ICONS_PATH = ADDON_PATH / "icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

USER_SUPPORT_EMAIL_SLUG = "help@ankipalace.com"

ANKI_MINOR = int(ANKI_VERSION.split(".")[2])

USER_FILES_PATH = Path(__file__).parent / "user_files"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"


RATIONALE_FOR_CHANGE_MAX_LENGTH = 1024
