from datetime import datetime, timezone

from aqt import mw


class Config:
    def __init__(self):
        self.config = mw.addonManager.getConfig("ankihub")
        self.base_url = self.config.get("baseUrl")
        self.token = self.config.get("token")
        self.last_sync = self.config.get("lastSync")

    def get_base_url(self) -> str:
        return self.base_url

    def save_token(self, token: str):
        self.token = token
        self.config["token"] = token
        mw.addonManager.writeConfig(__name__, self.config)

    def get_token(self) -> str:
        if self.token:
            return self.token
        return None

    def save_last_sync(self):
        date_object = datetime.now(tz=timezone.utc)
        date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")
        self.config["lastSync"] = date_time_str
        mw.addonManager.writeConfig(__name__, self.config)

    def get_last_sync(self):
        if self.last_sync:
            return self.last_sync
        return None
