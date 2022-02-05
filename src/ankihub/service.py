import json

import requests
from aqt import mw
from aqt.utils import showInfo


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


class ServiceApi:
    def __init__(self):
        self.config = Config()
        self.base_url = "http://localhost:8000/"
        if self.config.is_authenticated():
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": "Token " + self.config.token,
            }
        else:
            self.headers = {"Content-Type": "application/json"}

    def authenitcateUserGetToken(self, url: str, data: dict):
        if not url or not data:
            return None
        response = requests.post(
            self.base_url + url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(data),
        )
        if response.status_code == 200:
            token = json.loads(response.content)["token"]
            showInfo("token: " + token)
            self.config.write_token(token)
            return token
        else:
            return None

    def post_apkg(self, url, data, file):
        headers = {"Authorization": "Token " + self.config.token}
        return requests.post(
            self.base_url + url,
            headers=headers,
            files={"file": open(file, "rb")},
            data=data,
        )

    def post(self, url, data):
        return requests.post(
            self.base_url + url, headers=self.headers, data=json.dumps(data)
        )

    def get(self, url):
        return requests.get(self.base_url + url, headers=self.headers)
