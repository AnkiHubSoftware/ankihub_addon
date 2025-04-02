"""Code for the config of the add-on. This file also defines paths to files and directories used by the add-on
as well as some constants and code for setting up the profile folder and logger.
"""

import dataclasses
import gzip
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
from pathlib import Path
from shutil import copyfile, move, rmtree
from typing import Any, Callable, Dict, List, Optional

import aqt
import requests
import structlog
from anki import buildinfo
from anki.decks import DeckId
from anki.utils import is_win, point_version
from aqt.utils import askUser, showInfo
from mashumaro import field_options
from mashumaro.mixins.json import DataClassJSONMixin
from structlog.processors import CallsiteParameter
from structlog.typing import Processor

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
from .ankihub_client.models import Deck, UserDeckRelation
from .private_config_migrations import migrate_private_config
from .public_config_migrations import migrate_public_config

ADDON_PATH = Path(__file__).parent.absolute()

ANKIHUB_DB_FILENAME = "ankihub.db"
PRIVATE_CONFIG_FILENAME = ".private_config.json"

# the id of the Anki profile is saved under this key in Anki's profile config
# (profile configs are stored by Anki in prefs21.db in the anki base directory)
PROFILE_ID_FIELD_NAME = "ankihub_id"

TAG_FOR_INSTRUCTION_NOTES = "AnkiHub_Instructions"

# Only used for configuring the logger, a structlog logger is used for logging.
STD_LOGGER = logging.getLogger("ankihub")

# Log processors which are used for logs originating from structlog and logging.
SHARED_LOG_PROCESSORS: List[Processor] = [
    structlog.stdlib.filter_by_level,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.CallsiteParameterAdder(
        parameters=[
            CallsiteParameter.THREAD,
            CallsiteParameter.MODULE,
            CallsiteParameter.FUNC_NAME,
        ]
    ),
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]


def _serialize_datetime(x: datetime) -> str:
    return x.strftime(ANKIHUB_DATETIME_FORMAT_STR) if x else ""


def _deserialize_datetime(x: str) -> Optional[datetime]:
    return datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR) if x else None


class SuspendNewCardsOfExistingNotes(Enum):
    ALWAYS = "Always"
    NEVER = "Never"
    IF_SIBLINGS_SUSPENDED = "If siblings are suspended"


class BehaviorOnRemoteNoteDeleted(Enum):
    """What to do with the local note in Anki when it's deleted on AnkiHub."""

    DELETE_IF_NO_REVIEWS = "Delete if no reviews"
    NEVER_DELETE = "Never"


@dataclass
class DeckConfig(DataClassJSONMixin):
    anki_id: DeckId
    name: str
    behavior_on_remote_note_deleted: Optional[BehaviorOnRemoteNoteDeleted] = None
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
    download_full_deck_on_next_sync: bool = False
    subdecks_enabled: bool = (
        False  # whether deck is organized into subdecks by the add-on
    )
    suspend_new_cards_of_new_notes: bool = False
    suspend_new_cards_of_existing_notes: SuspendNewCardsOfExistingNotes = (
        SuspendNewCardsOfExistingNotes.IF_SIBLINGS_SUSPENDED
    )
    has_note_embeddings: bool = False

    @staticmethod
    def suspend_new_cards_of_new_notes_default(ah_did: uuid.UUID) -> bool:
        result = ah_did == config.anking_deck_id
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
    username: Optional[str] = ""
    decks: Dict[uuid.UUID, DeckConfig] = dataclasses.field(default_factory=dict)
    deck_extensions: Dict[int, DeckExtensionConfig] = dataclasses.field(
        default_factory=dict
    )
    ui: UIConfig = dataclasses.field(default_factory=UIConfig)
    # used to determine which migrations to apply
    api_version_on_last_sync: Optional[float] = None
    # the collection's schema modification time after an AnkiHub sync that triggers a full AnkiWeb sync.
    # used to determine whether to skip the full sync dialog
    # and choose "Upload" for the user automatically on next sync.
    schema_to_do_full_upload_for_once: Optional[int] = None
    last_sent_summary_date: Optional[date] = None
    feature_flags: dict = field(default_factory=dict)


class _Config:
    def __init__(self):
        # self.public_config is editable by the user using a built-in Anki feature.
        self.public_config: Optional[Dict[str, Any]] = None
        self._private_config: Optional[PrivateConfig] = None
        self._private_config_path: Optional[Path] = None
        self.token_change_hook: List[Callable[[], None]] = []
        self.app_url: Optional[str] = None
        self.s3_bucket_url: Optional[str] = None
        self.anking_deck_id: Optional[uuid.UUID] = None

    def setup_public_config_and_other_settings(self):
        migrate_public_config()
        self.load_public_config()

        if self.public_config.get("use_staging"):
            self.app_url = STAGING_APP_URL
            self.api_url = STAGING_API_URL
            self.s3_bucket_url = STAGING_S3_BUCKET_URL
            if staging_anking_deck_id := self.public_config.get(
                "staging_anking_deck_id"
            ):
                self.anking_deck_id = staging_anking_deck_id
            else:
                self.anking_deck_id = uuid.UUID("dfe7f548-f66e-4277-932b-c7a63db3223a")
        else:
            self.app_url = DEFAULT_APP_URL
            self.api_url = DEFAULT_API_URL
            self.s3_bucket_url = DEFAULT_S3_BUCKET_URL
            self.anking_deck_id = uuid.UUID("e77aedfe-a636-40e2-8169-2fce2673187e")

        # Override urls with environment variables if they are set.
        if app_url_from_env_var := os.getenv("ANKIHUB_APP_URL"):
            self.app_url = app_url_from_env_var
            self.api_url = f"{app_url_from_env_var}/api"

        if s3_url_from_env_var := os.getenv("S3_BUCKET_URL"):
            self.s3_bucket_url = s3_url_from_env_var

        if anking_deck_id_from_env_var := os.getenv("ANKING_DECK_ID"):
            self.anking_deck_id = uuid.UUID(anking_deck_id_from_env_var)

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
                LOGGER.exception("Failed to load private config. Overwriting it.")
                self._private_config = PrivateConfig()

        self._update_private_config()

    def _load_private_config(self) -> PrivateConfig:
        with open(self._private_config_path) as f:
            private_config_dict = json.load(f)

        try:
            migrate_private_config(private_config_dict)
        except Exception:
            LOGGER.exception("Failed to migrate private config.")

        result = PrivateConfig.from_dict(private_config_dict)
        return result

    def _update_private_config(self):
        with open(self._private_config_path, "w") as f:
            config_json = self._private_config.to_json()
            f.write(json.dumps(json.loads(config_json), indent=4, sort_keys=True))
        self.log_private_config(log_level=logging.DEBUG)

    def load_public_config(self) -> None:
        """For loading the public config from its file (after it has been changed)."""
        self.public_config = aqt.mw.addonManager.getConfig(ADDON_PATH.name)

    def save_token(self, token: str):
        token_changed = self.token() != token

        # aqt.mw.pm.set_ankihub_token(token)
        aqt.mw.pm.profile["thirdPartyAnkiHubToken"] = token

        if token_changed:
            for func in self.token_change_hook:
                # Prevent potential exceptions from being backpropagated to the caller.
                aqt.mw.taskman.run_on_main(func)

    def save_user_email(self, user_email: str):
        # aqt.mw.pm.set_ankihub_username(user_email)
        aqt.mw.pm.profile["thirdPartyAnkiHubUsername"] = user_email

    def save_username(self, username: str):
        self._private_config.username = username
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

    def set_download_full_deck_on_next_sync(
        self, ankihub_did: uuid.UUID, download_full_deck: bool
    ):
        self.deck_config(
            ankihub_did
        ).download_full_deck_on_next_sync = download_full_deck
        self._update_private_config()

    def save_last_sent_summary_date(self, last_summary_sent_date: Optional[date]):
        self._private_config.last_sent_summary_date = last_summary_sent_date
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

    def set_ankihub_deleted_notes_behavior(
        self, ankihub_did: uuid.UUID, note_delete_behavior: BehaviorOnRemoteNoteDeleted
    ):
        self.deck_config(
            ankihub_did
        ).behavior_on_remote_note_deleted = note_delete_behavior
        self._update_private_config()

    def set_feature_flags(self, feature_flags: Optional[dict]):
        self._private_config.feature_flags = feature_flags
        self._update_private_config()

    def get_feature_flags(self) -> Optional[dict]:
        return self._private_config.feature_flags

    def add_deck(
        self,
        name: str,
        ankihub_did: uuid.UUID,
        anki_did: DeckId,
        user_relation: UserDeckRelation,
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
        latest_udpate: Optional[datetime] = None,
        subdecks_enabled: bool = False,
        has_note_embeddings: bool = False,
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
            behavior_on_remote_note_deleted=behavior_on_remote_note_deleted,
            has_note_embeddings=has_note_embeddings,
        )
        # remove duplicates
        self.save_latest_deck_update(ankihub_did, latest_udpate)
        self._update_private_config()

    def update_deck(self, deck: Deck):
        """Update the deck config with the values from the Deck object."""
        deck_config = self.deck_config(deck.ah_did)

        # Only these fields are needed for the deck config
        deck_config.name = deck.name
        deck_config.user_relation = deck.user_relation
        deck_config.has_note_embeddings = deck.has_note_embeddings

        self._update_private_config()

    def remove_deck_and_its_extensions(self, ankihub_did: uuid.UUID) -> None:
        """Remove a deck. Also remove the deck extensions of the deck."""
        for deck_extension_id in config.deck_extensions_ids_for_ah_did(ankihub_did):
            config.remove_deck_extension(deck_extension_id)
        LOGGER.info("Removed deck extensions for deck", ah_did=ankihub_did)

        if self._private_config.decks.get(ankihub_did):
            self._private_config.decks.pop(ankihub_did)
            self._update_private_config()

    def log_private_config(self, log_level=logging.INFO):
        config_copy = deepcopy(self._private_config)
        LOGGER.log(log_level, "Private config", private_config=config_copy.to_dict())

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

    def deck_config_by_anki_did(self, anki_did: DeckId) -> Optional[DeckConfig]:
        decks = self._private_config.decks
        return next(
            (deck for deck in decks.values() if deck.anki_id == anki_did),
            None,
        )

    def token(self) -> Optional[str]:
        # return aqt.mw.pm.ankihub_token()
        return aqt.mw.pm.profile.get("thirdPartyAnkiHubToken")

    def user(self) -> Optional[str]:
        # return aqt.mw.pm.ankihub_username()
        return aqt.mw.pm.profile.get("thirdPartyAnkiHubUsername")

    def username(self) -> Optional[str]:
        return self._private_config.username

    def username_or_email(self) -> Optional[str]:
        return self.username() or self.user()

    def ui_config(self) -> UIConfig:
        return self._private_config.ui

    def set_ui_config(self, ui_config: UIConfig):
        self._private_config.ui = ui_config
        self._update_private_config()

    def deck_extension_ids(self) -> List[int]:
        return list(self._private_config.deck_extensions.keys())

    def get_last_sent_summary_date(self) -> Optional[date]:
        return self._private_config.last_sent_summary_date

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

    def remove_deck_extension(self, extension_id: int) -> None:
        self._private_config.deck_extensions.pop(extension_id)
        self._update_private_config()
        LOGGER.info("Removed deck extension.", extension_id=extension_id)

    def is_logged_in(self) -> bool:
        return bool(self.token())

    def set_api_version_on_last_sync(self, api_version: float) -> None:
        self._private_config.api_version_on_last_sync = api_version
        self._update_private_config()

    def set_schema_to_do_full_upload_for_once(self, col_schema: Optional[int]) -> None:
        self._private_config.schema_to_do_full_upload_for_once = col_schema
        self._update_private_config()

    def schema_to_do_full_upload_for_once(self) -> Optional[int]:
        return self._private_config.schema_to_do_full_upload_for_once


config = _Config()


def setup_profile_data_folder() -> bool:
    """Sets up the profile data folder for the currently open Anki profile.
    Returns False if the migration from the add-on version with no support for multiple Anki profiles
    needs yet to be done."""
    _assign_id_to_profile_if_not_exists()

    if not _maybe_migrate_profile_data_from_old_location():
        return False

    _maybe_migrate_addon_data_from_old_location()

    profile_files_path().mkdir(parents=True, exist_ok=True)

    LOGGER.info("Set up profile data folder.", anki_profile_id=get_anki_profile_id())

    return True


def _assign_id_to_profile_if_not_exists() -> None:
    """Assigns an id to the currently open profile if it doesn't have one."""
    if aqt.mw.pm.profile.get("ankihub_id") is not None:
        return

    new_profile_id = uuid.uuid4()
    _set_anki_profile_id(str(new_profile_id))

    LOGGER.info("Assigned new id to Anki profile.", anki_profile_id=new_profile_id)


def get_anki_profile_id() -> str:
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
    cur_profile_id = get_anki_profile_id()
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
        LOGGER.exception(
            "Failed to migrate profile data from user_files to profile folder."
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
            LOGGER.info(
                "Migrated add-on data for profile.", profile_folder_name=file.name
            )


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


def _structlog_formatter(renderer) -> logging.Formatter:
    formatter = structlog.stdlib.ProcessorFormatter(
        # These run ONLY on `logging` entries that do NOT originate within
        # structlog.
        foreign_pre_chain=SHARED_LOG_PROCESSORS,
        # These run on ALL entries after the pre_chain is done.
        processors=[
            # Remove _record & _from_structlog.
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    return formatter


def setup_logger():
    structlog.configure(
        processors=SHARED_LOG_PROCESSORS
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    STD_LOGGER.propagate = False
    STD_LOGGER.setLevel(logging.DEBUG)

    _setup_stdout_handler()
    _setup_file_handler()

    if ADDON_VERSION != "dev":
        _setup_datadog_handler()

    _fix_runtime_error_on_shutdown()


def _fix_runtime_error_on_shutdown() -> None:
    # Fix 'RuntimeError: wrapped C/C++ object of type ErrorHandler has been deleted' on shutdown
    # by making the sentry logger write to stdout instead of stderr.
    # sys.stderr is overwritten by aqt.ErrorHandler to itself, which is deleted on shutdown.
    # Without this, the logger would try to write to the deleted ErrorHandler object and raise an error.
    sentry_logger = logging.getLogger("sentry_sdk.errors")
    for handler in sentry_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stdout)


def _setup_stdout_handler() -> None:
    stdout_handler_ = _stdout_handler()
    stdout_handler_.setLevel(logging.INFO)
    stdout_handler_.setFormatter(
        _structlog_formatter(
            # Colors were causing issues on Windows, so we disable them there.
            structlog.dev.ConsoleRenderer(colors=not is_win),
        )
    )
    STD_LOGGER.addHandler(stdout_handler_)


def _setup_file_handler() -> None:
    log_file_path().parent.mkdir(parents=True, exist_ok=True)
    file_handler_ = _file_handler()
    file_handler_.setLevel(
        logging.DEBUG
        if config.public_config.get("debug_level_logs", False)
        else logging.INFO
    )
    file_handler_.setFormatter(
        _structlog_formatter(
            structlog.dev.ConsoleRenderer(colors=False),
        )
    )
    STD_LOGGER.addHandler(file_handler_)


def _setup_datadog_handler():
    datadog_handler = DatadogLogHandler()
    datadog_handler.setLevel(logging.INFO)
    datadog_handler.setFormatter(
        _structlog_formatter(structlog.processors.JSONRenderer())
    )
    STD_LOGGER.addHandler(datadog_handler)


class DatadogLogHandler(logging.Handler):
    """
    A custom logging handler that sends logs to Datadog.

    This handler buffers log records and sends them to Datadog either when the buffer is full
    or when a certain amount of time has passed since the last send operation.
    """

    def __init__(self, capacity: int = 50, send_interval: int = 60 * 5):
        super().__init__()
        self.buffer: List[logging.LogRecord] = []
        self.capacity: int = capacity
        self.send_interval: int = send_interval
        self.last_send_time: float = time.time()
        self.createLock()
        self.flush_thread: threading.Thread = threading.Thread(
            target=self._periodic_flush
        )
        # Make the thread a daemon so that it doesn't prevent Anki from closing
        self.flush_thread.daemon = True
        self.flush_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        with self.lock:
            self.buffer.append(record)
            if len(self.buffer) >= self.capacity:
                self.flush(in_background=True)

    def flush(self, in_background=False) -> None:
        # flush is also called when the logging module shuts down when Anki is closing.
        # in_background=False is used to not create a new thread when the add-on is closing,
        # as this leads to an error in the shutdown, because at this point no new threads can be created.
        feature_flags = config.get_feature_flags()

        if not feature_flags.get("send_addon_logs_to_datadog", False):
            with self.lock:
                # Clear the buffer to prevent it from growing indefinitely.
                self.buffer = []
            return

        with self.lock:
            if not self.buffer:
                return
            records_to_send: List[logging.LogRecord] = self.buffer
            self.buffer = []
            self.last_send_time = time.time()
            if in_background:
                threading.Thread(
                    target=self._send_logs_to_datadog, args=(records_to_send,)
                ).start()
            else:
                self._send_logs_to_datadog(records_to_send)

    def _periodic_flush(self) -> None:
        while True:
            time.sleep(self.send_interval)
            with self.lock:
                if time.time() - self.last_send_time >= self.send_interval:
                    self.flush(in_background=True)

    def _send_logs_to_datadog(self, records: List[logging.LogRecord]) -> None:
        body = [
            {
                "ddsource": "anki_addon",
                "ddtags": f"addon_version:{ADDON_VERSION},anki_version:{ANKI_VERSION}",
                "hostname": socket.gethostname(),
                "service": "ankihub_addon",
                "username": config.username_or_email(),
                **json.loads(self.format(record)),
            }
            for record in records
        ]

        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "DD-API-KEY": "pub573b4cee7263687d0323a12cf5a30a52",
        }

        compressed_body = gzip.compress(json.dumps(body).encode())

        try:
            response = requests.post(
                "https://http-intake.logs.datadoghq.com/api/v2/logs",
                headers=headers,
                data=compressed_body,
            )
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:  # pragma: no cover
            LOGGER.warning(
                "Connection error or timeout when sending logs to Datadog.", exc_info=e
            )
            return
        except requests.exceptions.RequestException:  # pragma: no cover
            LOGGER.exception(
                "An unexpected error occurred when sending logs to Datadog"
            )
            return

        if response.status_code != 202:  # pragma: no cover
            LOGGER.warning(
                "Unexpected status code when sending logs to Datadog.",
                status_code=response.status_code,
                response_text=response.text,
            )


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
url_flashcard_selector = (
    lambda deck_id: f"{config.app_url}/ai/{deck_id}/flashcard-selector"
)  # noqa: E731
url_flashcard_selector_embed = (
    lambda deck_id: f"{config.app_url}/ai/{deck_id}/flashcard-selector-embed?is_on_anki=true"
)  # noqa: E731
url_plans_page = lambda: f"{config.app_url}/memberships/plans/"  # noqa: E731
url_mh_integrations_preview = (
    lambda resource_slug: f"{config.app_url}/integrations/mcgraw-hill/preview/{resource_slug}"
)  # noqa: E731
url_login = lambda: f"{config.app_url}/accounts/login"  # noqa: E731

ANKIHUB_NOTE_TYPE_FIELD_NAME = "ankihub_id"
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

# Daily review summaries are sent for days which were at least this many days ago.
# We are sending the summaries for days in the past to give users time to sync their reviews
# from other devices.
DELAY_FOR_SENDING_DAILY_REVIEW_SUMMARIES = 3


def get_end_cutoff_date_for_sending_review_summaries() -> date:
    return date.today() - timedelta(days=DELAY_FOR_SENDING_DAILY_REVIEW_SUMMARIES)


ANKI_VERSION_23_10_00 = 231000

ANKING_NOTE_TYPES_ADDON_ANKIWEB_ID = 952691989
ANKING_NOTE_TYPES_ADDON_MODULE_NAME = "anking_note_types"

PROJEKTANKI_NOTE_TYPES_ADDON_ANKIWEB_ID = 2058530482
PROJEKTANKI_NOTE_TYPES_ADDON_MODULE_NAME = "projekt_anki_notetypes"


def is_anking_note_types_addon_installed():
    addon_dir_names = set(x.dir_name for x in aqt.mw.addonManager.all_addon_meta())
    return (
        str(ANKING_NOTE_TYPES_ADDON_ANKIWEB_ID) in addon_dir_names
        or ANKING_NOTE_TYPES_ADDON_MODULE_NAME in addon_dir_names
    )


def is_projektanki_note_types_addon_installed():
    addon_dir_names = set(x.dir_name for x in aqt.mw.addonManager.all_addon_meta())
    return (
        str(PROJEKTANKI_NOTE_TYPES_ADDON_ANKIWEB_ID) in addon_dir_names
        or PROJEKTANKI_NOTE_TYPES_ADDON_MODULE_NAME in addon_dir_names
    )
