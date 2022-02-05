from aqt import mw


class Config:
    def __init__(self):
        self.addon = "ankihub"
        self.config = mw.addonManager.getConfig(self.addon)
        self.user = self.config.get("user")
        self.token = self.user.get("token")

    def is_authenticated(self) -> bool:
        return True if self.token else False

    def signout(self):
        default = mw.addonManager.addonConfigDefaults(self.addon)
        mw.addonManager.writeConfig(__name__, default)

    def write_token(self, token: str) -> None:
        # TODO needs test
        self.token = token
        mw.addonManager.writeConfig(__name__, self.config)
