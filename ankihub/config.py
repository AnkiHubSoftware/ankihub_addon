import json
import os
from datetime import datetime, timezone

from aqt import mw


class Config:
    def __init__(self):
        # self._public_config is editable by the user.
        self.public_config = mw.addonManager.getConfig("ankihub")
        # This is the location for private config which is only managed by our code
        # and is not exposed to the user.
        # See https://addon-docs.ankiweb.net/addon-config.html#user-files
        user_files_path = os.path.join(
            mw.addonManager.addonsFolder("ankihub"), "user_files"
        )
        self._private_config_file_path = os.path.join(user_files_path, "private_config.json")
        if not os.path.exists(self._private_config_file_path):
            self.private_config = {}
            with open(self._private_config_file_path, "w") as f:
                f.write(json.dumps(self.private_config))
        else:
            with open(self._private_config_file_path) as f:
                self.private_config = json.load(f)

    def _update_private_config(self, config_data: dict):
        with open(self._private_config_file_path, "w") as f:
            f.write(json.dumps(config_data))

    def save_token(self, token: str):
        self.private_config["token"] = token
        self._update_private_config(self.private_config)

    def get_token(self) -> str:
        return self.private_config.get("token")

    def save_last_sync(self):
        date_object = datetime.now(tz=timezone.utc)
        date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")
        self.private_config["lastSync"] = date_time_str
        self._update_private_config(self.private_config)

    def get_last_sync(self) -> str:
        return self.private_config["lastSync"]
