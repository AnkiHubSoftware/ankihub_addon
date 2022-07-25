import dataclasses
import json
import os
from datetime import datetime
from json import JSONDecodeError
from pprint import pformat
from typing import Any, Callable, Dict, Optional

from aqt import mw

from . import LOGGER


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
        config_dict = dataclasses.asdict(self.private_config)
        LOGGER.debug(f"PrivateConfig init:\n {pformat(config_dict)}")
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
            LOGGER.debug(f"Updated PrivateConfig:\n {pformat(config_dict)}")

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


config: Config = Config()
