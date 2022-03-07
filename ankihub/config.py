import json
import os
from datetime import datetime, timezone

from aqt import mw


class Config:
    def __init__(self):
        self._config = mw.addonManager.getConfig("ankihub")
        user_files_path = os.path.join(
            mw.addonManager.addonsFolder("ankihub"), "user_files"
        )
        self._user_config_file_path = os.path.join(user_files_path, "userConfig.json")
        with open(self._user_config_file_path) as user_config_file:
            self.user_config = json.load(user_config_file)

    def _update_user_config(self, config_data: dict):
        with open(self._user_config_file_path, "w") as user_config_file:
            user_config_file.write(json.dumps(config_data))

    def save_token(self, token: str):
        self.token = token
        self.user_config["token"] = token
        self._update_user_config(self.user_config)

    def get_token(self) -> str:
        return self.user_config["token"]

    def save_last_sync(self):
        date_object = datetime.now(tz=timezone.utc)
        date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")
        self.user_config["lastSync"] = date_time_str
        self._update_user_config(self.user_config)

    def get_last_sync(self) -> str:
        return self.user_config["lastSync"]
