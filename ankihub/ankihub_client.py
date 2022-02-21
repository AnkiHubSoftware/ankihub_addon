import json

import requests

from ankihub.config import Config


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self):
        self.base_url = "http://localhost:8000/"
        self.config = Config()
        if self.config.is_authenticated():
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": "Token " + self.config.token,
            }
        else:
            self.headers = {"Content-Type": "application/json"}

    def authenticate_user(self, url: str, data: dict) -> str:
        """Authenticate the user and return their token."""
        token = ""
        response = requests.post(
            self.base_url + url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(data),
        )
        if response.status_code == 200:
            token = json.loads(response.content)["token"]
            self.config.write_token(token)
        return token

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

    def submit_change(self):
        print("Submitting change")

    def submit_new_note(self):
        print("Submitting new note")
