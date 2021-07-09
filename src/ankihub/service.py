from aqt import mw
from aqt.utils import showInfo
import requests
import json


class Config:
    def __init__(self):
        self.addon = __name__.split(".")[0]

    def get(self):
        return mw.addonManager.getConfig(self.addon)

    def isAuthenticated(self) -> bool:
        config = self.get()
        return True if config['user']['token'] else False

    def writeToken(self, token: str) -> None:
        config = self.get()
        showInfo("token"+token)
        config['user']['token'] = token
        mw.addonManager.writeConfig(__name__, config)


class ServiceApi:
    def __init__(self):
        self.config = Config()
        self.base_url = "http://localhost:8000/"
        if self.config.isAuthenticated():
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": "Token " + self.token
            }
        else:
            self.headers = {
                "Content-Type": "application/json",
            }

    def getConfigToken(self) -> str:
        value = self.config.get()
        return value['user']['token']

    def authenitcateUserGetToken(self, url: str, data: dict):
        if not url or not data:
            return None
        response = requests.post(
                self.base_url + url,
                headers={
                    "Content-Type": "application/json",
                },
                data=json.dumps(data)
            )
        if response.status_code == 200:
            token = json.loads(response.content)['token']
            showInfo('token: '+token)
            self.config.writeToken(token)
            return token
        else:
            return None

    def post(self, url, data):
        return requests.post(
            self.base_url + url,
            headers=self.headers,
            data=json.dumps(data)
            )

    def get(self, url):
        return requests.get(
            self.base_url + url,
            headers=self.headers
            )
