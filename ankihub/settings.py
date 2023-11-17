"""Code for the config of the add-on. This file also defines paths to files and directories used by the add-on
as well as some constants and code for setting up the profile folder and logger.
"""
import dataclasses
import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pprint import pformat
from shutil import copyfile, move, rmtree
from typing import Any, Callable, Dict, List, Optional

import aqt
from anki import buildinfo
from anki.decks import DeckId
from anki.utils import point_version
from aqt.utils import askUser, showInfo
from mashumaro import field_options
from mashumaro.mixins.json import DataClassJSONMixin

from . import LOGGER
from .ankihub_client import (
    ANKIHUB_DATETIME_FORMAT_STR,
    DEFAULT_API_URL,
    DEFAULT_APP_URL,
    DEFAULT_S3_BUCKET_URL,
    STAGING_API_URL,
    STAGING_APP_URL,
    STAGING_S3_BUCKET_URL,
    DeckExtension,
)
from .ankihub_client.models import UserDeckRelation
from .private_config_migrations import migrate_private_config
from .public_config_migrations import migrate_public_config

ADDON_PATH = Path(__file__).parent.absolute()

ANKIHUB_DB_FILENAME = "ankihub.db"
PRIVATE_CONFIG_FILENAME = ".private_config.json"

# the id of the Anki profile is saved under this key in Anki's profile config
# (profile configs are stored by Anki in prefs21.db in the anki base directory)
PROFILE_ID_FIELD_NAME = "ankihub_id"

# Id of the AnKing Overhaul deck
ANKING_DECK_ID = uuid.UUID("e77aedfe-a636-40e2-8169-2fce2673187e")


def _serialize_datetime(x: datetime) -> str:
    return x.strftime(ANKIHUB_DATETIME_FORMAT_STR) if x else ""


def _deserialize_datetime(x: str) -> Optional[datetime]:
    return datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR) if x else None


class SuspendNewCardsOfExistingNotes(Enum):
    ALWAYS = "Always"
    NEVER = "Never"
    IF_SIBLINGS_SUSPENDED = "If siblings are suspended"


@dataclass
class DeckConfig(DataClassJSONMixin):
    anki_id: DeckId
    name: str
    user_relation: UserDeckRelation = UserDeckRelation.SUBSCRIBER
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            serialize=_serialize_datetime,
            deserialize=_deserialize_datetime,
        ),
        default=None,
    )
    latest_media_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            serialize=_serialize_datetime,
            deserialize=_deserialize_datetime,
        ),
        default=None,
    )
    subdecks_enabled: bool = (
        False  # whether deck is organized into subdecks by the add-on
    )
    suspend_new_cards_of_new_notes: bool = False
    suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes = (
        SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED
    )

    @staticmethod
    def suspend_new_cards_of_new_notes_default(ah_did: uuid.UUID) -> bool:
        result = ah_did == ANKING_DECK_ID
        return result

    @staticmethod
    def suspend_new_cards_of_existing_notes_default() -> SuspendNewCardsOfExistingNotes:
        return SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED


@dataclass
class DeckExtensionConfig(DataClassJSONMixin):
    ah_did: uuid.UUID
    owner_id: int
    name: str
    tag_group_name: str
    description: str
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            serialize=_serialize_datetime,
            deserialize=_deserialize_datetime,
        ),
        default=None,
    )


@dataclass
class UIConfig(DataClassJSONMixin):
    # whether the trees in the browser sidebar are expanded or collapsed
    ankihub_tree_expanded: bool = True
    updated_today_tree_expanded: bool = False


@dataclasses.dataclass
class PrivateConfig(DataClassJSONMixin):
    token: str = ""
    user: str = ""
    decks: Dict[uuid.UUID, DeckConfig] = dataclasses.field(default_factory=dict)
    deck_extensions: Dict[int, DeckExtensionConfig] = dataclasses.field(
        default_factory=dict
    )
    ui: UIConfig = dataclasses.field(default_factory=UIConfig)
    # used to determine which migrations to apply
    api_version_on_last_sync: Optional[float] = None


class _Config:
    def __init__(self):
        # self.public_config is editable by the user using a built-in Anki feature.
        self.public_config: Optional[Dict[str, Any]] = None
        self._private_config: Optional[PrivateConfig] = None
        self._private_config_path: Optional[Path] = None
        self.token_change_hook: Optional[Callable[[], None]] = None
        self.subscriptions_change_hook: Optional[Callable[[], None]] = None
        self.app_url: Optional[str] = None
        self.s3_bucket_url: Optional[str] = None

    def setup_public_config_and_urls(self):
        migrate_public_config()
        self.load_public_config()

        if self.public_config.get("use_staging"):
            self.app_url = STAGING_APP_URL
            self.api_url = STAGING_API_URL
            self.s3_bucket_url = STAGING_S3_BUCKET_URL
        else:
            self.app_url = DEFAULT_APP_URL
            self.api_url = DEFAULT_API_URL
            self.s3_bucket_url = DEFAULT_S3_BUCKET_URL

        # Override urls with environment variables if they are set.
        if app_url_from_env_var := os.getenv("ANKIHUB_APP_URL"):
            self.app_url = app_url_from_env_var
            self.api_url = f"{app_url_from_env_var}/api"

        if s3_url_from_env_var := os.getenv("S3_BUCKET_URL"):
            self.s3_bucket_url = s3_url_from_env_var

    def setup_private_config(self):
        # requires the profile setup to be completed unlike self.setup_pbulic_config
        self._private_config_path = private_config_path()
        if not self._private_config_path.exists():
            self._private_config = PrivateConfig()
        else:
            try:
                self._private_config = self._load_private_config()
            except JSONDecodeError:
                # TODO Instead of overwriting, query AnkiHub for config values.
                self._private_config = PrivateConfig()

        self._update_private_config()
        self._log_private_config()

    def _load_private_config(self) -> PrivateConfig:
        with open(self._private_config_path) as f:
            private_config_dict = json.load(f)

        try:
            migrate_private_config(private_config_dict)
        except Exception:
            LOGGER.warning("Failed to migrate private config")

        result = PrivateConfig.from_dict(private_config_dict)
        return result

    def _update_private_config(self):
        with open(self._private_config_path, "w") as f:
            config_json = self._private_config.to_json()
            f.write(json.dumps(json.loads(config_json), indent=4, sort_keys=True))
        self._log_private_config()

    def load_public_config(self) -> None:
        """For loading the public config from its file (after it has been changed)."""
        self.public_config = aqt.mw.addonManager.getConfig(ADDON_PATH.name)

    def save_token(self, token: str):
        self._private_config.token = token
        self._update_private_config()
        if self.token_change_hook:
            self.token_change_hook()

    def save_user_email(self, user_email: str):
        self._private_config.user = user_email
        self._update_private_config()

    def save_latest_deck_update(
        self, ankihub_did: uuid.UUID, latest_update: Optional[datetime]
    ):
        self.deck_config(ankihub_did).latest_update = latest_update
        self._update_private_config()

    def save_latest_deck_media_update(
        self, ankihub_did: uuid.UUID, latest_media_update: Optional[datetime]
    ):
        self.deck_config(ankihub_did).latest_media_update = latest_media_update
        self._update_private_config()

    def set_subdecks(self, ankihub_did: uuid.UUID, subdecks: bool):
        self.deck_config(ankihub_did).subdecks_enabled = subdecks
        self._update_private_config()

    def set_suspend_new_cards_of_new_notes(self, ankihub_did: uuid.UUID, suspend: bool):
        self.deck_config(ankihub_did).suspend_new_cards_of_new_notes = suspend
        self._update_private_config()

    def set_suspend_new_cards_of_existing_notes(
        self, ankihub_did: uuid.UUID, suspend: SuspendNewCardsOfExistingNotes
    ):
        self.deck_config(ankihub_did).suspend_new_cards_of_existing_notes = suspend
        self._update_private_config()

    def add_deck(
        self,
        name: str,
        ankihub_did: uuid.UUID,
        anki_did: DeckId,
        user_relation: UserDeckRelation,
        latest_udpate: Optional[datetime] = None,
        subdecks_enabled: bool = False,
    ) -> None:
        """Add deck to the list of installed decks."""
        self._private_config.decks[ankihub_did] = DeckConfig(
            name=name,
            anki_id=DeckId(anki_did),
            user_relation=user_relation,
            subdecks_enabled=subdecks_enabled,
            suspend_new_cards_of_new_notes=DeckConfig.suspend_new_cards_of_new_notes_default(
                ankihub_did
            ),
        )
        # remove duplicates
        self.save_latest_deck_update(ankihub_did, latest_udpate)
        self._update_private_config()

        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def remove_deck(self, ankihub_did: uuid.UUID) -> None:
        """Remove deck from list of installed decks."""
        if self._private_config.decks.get(ankihub_did):
            self._private_config.decks.pop(ankihub_did)
            self._update_private_config()
        if self.subscriptions_change_hook:
            self.subscriptions_change_hook()

    def _log_private_config(self):
        config_dict = dataclasses.asdict(self._private_config)
        if config_dict["token"]:
            config_dict["token"] = "REDACTED"
        LOGGER.info(f"private config:\n{pformat(config_dict)}")

    def set_home_deck(self, ankihub_did: uuid.UUID, anki_did: DeckId):
        self.deck_config(ankihub_did).anki_id = anki_did
        self._update_private_config()

    def deck_ids(self) -> List[uuid.UUID]:
        """Return the ankihub deck ids of the currently locally
        installed decks."""
        return list(self._private_config.decks.keys())

    def get_deck_uuid_by_did(self, did: DeckId) -> Optional[uuid.UUID]:
        decks = self._private_config.decks
        return next(
            (key for key in decks.keys() if decks[key].anki_id == did),
            None,
        )

    def deck_config(self, ankihub_did: uuid.UUID) -> Optional[DeckConfig]:
        return self._private_config.decks.get(ankihub_did)

    def token(self) -> Optional[str]:
        return self._private_config.token

    def user(self) -> Optional[str]:
        return self._private_config.user

    def ui_config(self) -> UIConfig:
        return self._private_config.ui

    def set_ui_config(self, ui_config: UIConfig):
        self._private_config.ui = ui_config
        self._update_private_config()

    def deck_extension_ids(self) -> List[int]:
        return list(self._private_config.deck_extensions.keys())

    def create_or_update_deck_extension_config(self, extension: DeckExtension) -> None:
        latest_update = (
            extension_config.latest_update
            if (extension_config := self.deck_extension_config(extension.id))
            else None
        )

        self._private_config.deck_extensions[extension.id] = DeckExtensionConfig(
            ah_did=extension.ah_did,
            name=extension.name,
            owner_id=extension.owner_id,
            tag_group_name=extension.tag_group_name,
            description=extension.description,
            latest_update=latest_update,
        )
        self._update_private_config()

    def save_latest_extension_update(
        self, extension_id: int, latest_update: datetime
    ) -> None:
        self.deck_extension_config(extension_id).latest_update = latest_update
        self._update_private_config()

    def deck_extension_config(self, extension_id: int) -> Optional[DeckExtensionConfig]:
        return self._private_config.deck_extensions.get(extension_id)

    def deck_extensions_ids_for_ah_did(self, ah_did: uuid.UUID) -> List[int]:
        result = [
            extension_id
            for extension_id in self.deck_extension_ids()
            if self.deck_extension_config(extension_id).ah_did == ah_did
        ]
        return result

    def is_logged_in(self) -> bool:
        return bool(self.token())

    def set_api_version_on_last_sync(self, api_version: float) -> None:
        self._private_config.api_version_on_last_sync = api_version
        self._update_private_config()


config = _Config()


def setup_profile_data_folder() -> bool:
    """Sets up the profile data folder for the currently open Anki profile.
    Returns False if the migration from the add-on version with no support for multiple Anki profiles
    needs yet to be done."""
    _assign_id_to_profile_if_not_exists()
    LOGGER.info(f"Anki profile id: {_get_anki_profile_id()}")

    if not _maybe_migrate_profile_data_from_old_location():
        return False

    _maybe_migrate_addon_data_from_old_location()

    profile_files_path().mkdir(parents=True, exist_ok=True)

    return True


def _assign_id_to_profile_if_not_exists() -> None:
    """Assigns an id to the currently open profile if it doesn't have one."""
    if aqt.mw.pm.profile.get("ankihub_id") is not None:
        return

    new_profile_id = uuid.uuid4()
    _set_anki_profile_id(str(new_profile_id))

    LOGGER.info(f"Assigned new id to Anki profile: {_get_anki_profile_id()}")


def _get_anki_profile_id() -> str:
    """Returns the id of the currently open Anki profile."""
    return aqt.mw.pm.profile[PROFILE_ID_FIELD_NAME]


def _set_anki_profile_id(profile_id: str) -> None:
    """Sets the id of the currently open Anki profile."""
    aqt.mw.pm.profile[PROFILE_ID_FIELD_NAME] = profile_id
    aqt.mw.pm.save()


def addon_dir_path() -> Path:
    addon_dir_name = aqt.mw.addonManager.addonFromModule(__name__)
    result = Path(aqt.mw.addonManager.addonsFolder(addon_dir_name))
    return result


def user_files_path() -> Path:
    # The contents of the user_files folder are retained during updates.
    # See https://addon-docs.ankiweb.net/addon-config.html#user-files
    result = addon_dir_path() / "user_files"
    return result


def profile_files_path() -> Path:
    """Path to the add-on data for this Anki profile."""
    # we need an id instead of using the profile name because profiles can be renamed
    cur_profile_id = _get_anki_profile_id()
    result = ankihub_base_path() / cur_profile_id
    return result


def ankihub_db_path() -> Path:
    result = profile_files_path() / ANKIHUB_DB_FILENAME
    return result


def private_config_path() -> Path:
    result = profile_files_path() / PRIVATE_CONFIG_FILENAME
    return result


def _profile_data_exists_at_old_location() -> bool:
    result = (user_files_path() / PRIVATE_CONFIG_FILENAME).exists()
    return result


def _maybe_migrate_profile_data_from_old_location() -> bool:
    """Migration of add-on files from before the add-on added support for multiple Anki profiles was added
    into a profile-specific folder.
    Returns True if the data was migrated and False if it remains at the old location.
    """
    if not _profile_data_exists_at_old_location():
        LOGGER.info("No data to migrate.")
        return True

    if len(aqt.mw.pm.profiles()) > 1:
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
            if not _file_should_be_migrated(file):
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
        if not _file_should_be_migrated(file):
            continue

        file.unlink()

    return True


def _file_should_be_migrated(file_path: Path) -> bool:
    result = (
        file_path.is_file()
        and not str(file_path.name).startswith("README")
        and not re.match(r".+\.log(\.\d+)?$", file_path.name)
    )
    return result


def _maybe_migrate_addon_data_from_old_location() -> None:
    """Migrate profile data folders from user_files to ankihub_base_path() if they exist in user_files."""
    ankihub_base_path().mkdir(parents=True, exist_ok=True)

    for file in user_files_path().glob("*"):
        if not file.is_dir():
            continue

        try:
            uuid.UUID(file.name)
        except ValueError:
            continue
        else:
            # Only move the folder if it's name is a uuid, otherwise it's not an add-on data folder
            move(file, ankihub_base_path() / file.name)
            LOGGER.info(f"Migrated add-on data for profile {file.name}")


def ankihub_base_path() -> Path:
    """Path to the folder where the add-on stores its data."""
    if path_from_env_var := os.getenv("ANKIHUB_BASE_PATH"):
        return Path(path_from_env_var)

    anki_base = Path(aqt.mw.pm._default_base())
    result = anki_base.parent / "AnkiHub"
    return result


def log_file_path() -> Path:
    """Path to the add-on log file."""
    result = ankihub_base_path() / "ankihub.log"
    result.parent.mkdir(parents=True, exist_ok=True)
    return result


def _stdout_handler() -> logging.Handler:
    return logging.StreamHandler(stream=sys.stdout)


def _file_handler() -> logging.Handler:
    return RotatingFileHandler(
        log_file_path(), maxBytes=3000000, backupCount=5, encoding="utf-8"
    )


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(levelname)s %(asctime)s %(module)s %(process)d %(thread)d %(message)s"
    )


def setup_logger():
    log_file_path().parent.mkdir(parents=True, exist_ok=True)
    LOGGER.propagate = False
    LOGGER.setLevel(logging.DEBUG)

    setup_stdout_handler()
    setup_file_handler()


def setup_stdout_handler() -> None:
    stdout_handler_ = _stdout_handler()
    stdout_handler_.setLevel(logging.INFO)
    stdout_handler_.setFormatter(_formatter())
    LOGGER.addHandler(stdout_handler_)


def setup_file_handler() -> None:
    file_handler_ = _file_handler()
    file_handler_.setLevel(
        logging.DEBUG
        if config.public_config.get("debug_level_logs", False)
        else logging.INFO
    )
    file_handler_.setFormatter(_formatter())
    LOGGER.addHandler(file_handler_)


version_file = Path(__file__).parent / "VERSION"
with version_file.open() as f:
    ADDON_VERSION: str = f.read().strip()

try:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    ANKIWEB_ID = manifest.get("ankiweb_id")
except (FileNotFoundError, KeyError):
    ANKIWEB_ID = 1322529746


url_view_note = lambda: f"{config.app_url}/decks/notes/"  # noqa: E731
url_view_note_history = (
    lambda: f"{config.app_url}/decks/{{ankihub_did}}/suggestions/?search=note:{{ankihub_nid}},state:closed"
)  # noqa: E731
url_view_deck = lambda: f"{config.app_url}/decks/"  # noqa: E731
url_help = lambda: f"{config.app_url}/help"  # noqa: E731
url_decks = lambda: f"{config.app_url}/explore"  # noqa: E731
url_deck_base = lambda: f"{config.app_url}/decks"  # noqa: E731


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
ICONS_PATH = ADDON_PATH / "gui/icons"

TOKEN_SLUG = "token"
USER_EMAIL_SLUG = "user_email"

USER_SUPPORT_EMAIL_SLUG = "support@ankihub.net"


ANKI_VERSION = buildinfo.version
ANKI_INT_VERSION = point_version()

USER_FILES_PATH = Path(__file__).parent / "user_files"


class AnkiHubCommands(Enum):
    CHANGE = "Suggest a change"
    NEW = "Suggest a new note"


RATIONALE_FOR_CHANGE_MAX_LENGTH = 1024

ANKI_VERSION_23_10_00 = 231000
