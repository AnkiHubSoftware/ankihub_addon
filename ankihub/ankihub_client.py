import requests

from ankihub.config import Config
from ankihub.constants import API_URL_BASE


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self):
        self._headers = {"Content-Type": "application/json"}
        self._config = Config()
        self._base_url = API_URL_BASE
        if self._config.get_token():
            token = self._config.get_token()
            self._headers["Authorization"] = f"Token {token}"

    def _call_api(self, method, endpoint, data=None, params=None):
        response = requests.request(
            method=method,
            headers=self._headers,
            url=f"{self._base_url}{endpoint}",
            json=data,
            params=params,
        )
        response.raise_for_status()
        return response

    def login(self, credentials: dict):
        response = self._call_api("POST", "/login/", credentials)
        token = response.json()["token"]
        self._config.save_token(token)
        self._headers["Authorization"] = f"Token {token}"

    def signout(self):
        self._config.save_token("")
        self._headers["Authorization"] = ""

    def upload_deck(self, key: str):
        response = self._call_api("POST", "/decks/", data={"key": key})
        return response

    def get_deck_updates(self, deck_id: str) -> dict:
        response = self._call_api(
            "GET",
            f"/decks/{deck_id}/updates",
            params={"since": f"{self._config.get_last_sync()}"},
        )
        self._config.save_last_sync()
        return response.json()

    def get_note_by_anki_id(self, anki_id: str) -> dict:
        return self._call_api("GET", f"/notes/{anki_id}").json()

    def create_note_suggestion(self, note_suggestion: dict, note_id: int) -> dict:
        return self._call_api("POST", f"/notes/{note_id}/suggestion/", note_suggestion)
