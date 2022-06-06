import dataclasses
import json
import os
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import Any, Dict

from aqt import mw, qDebug


@dataclasses.dataclass
class PrivateConfig:
    token: str = ""
    user: str = ""
    decks: Dict[int, Dict[str, Any]] = dataclasses.field(default_factory=dict)
    last_sync: str = ""


class Config:
    def __init__(self):
        # self.public_config is editable by the user.
        self.public_config = mw.addonManager.getConfig("ankihub")
        # This is the location for private config which is only managed by our code
        # and is not exposed to the user.
        # See https://addon-docs.ankiweb.net/addon-config.html#user-files
        user_files_path = os.path.join(
            mw.addonManager.addonsFolder("ankihub"), "user_files"
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
        qDebug(f"Private config: {self.private_config}")

    def new_config(self):
        private_config = PrivateConfig()
        config_dict = dataclasses.asdict(private_config)
        with open(self._private_config_file_path, "w") as f:
            f.write(json.dumps(config_dict))
        return private_config

    def _update_private_config(self):
        with open(self._private_config_file_path, "w") as f:
            config_dict = dataclasses.asdict(self.private_config)
            f.write(json.dumps(config_dict))

    def save_token(self, token: str):
        self.private_config.token = token
        self._update_private_config()

    def save_user_email(self, user_email: str):
        self.private_config.user = user_email
        self._update_private_config()

    def save_last_sync(self, time=None):
        if time:
            date_object = datetime.strptime(time, "%Y-%m-%dT%H:%M:%S.%f%z")
        else:
            date_object = datetime.now(tz=timezone.utc)
        date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")
        self.private_config.last_sync = date_time_str
        self._update_private_config()

    def save_subscription(self, ankihub_did: int, anki_did: int, creator: bool = False):
        self.private_config.decks[ankihub_did] = {
            "anki_id": anki_did,
            "creator": creator,
        }
        # remove duplicates
        self.save_last_sync()
        self._update_private_config()
