import requests
from ankihub.config import Config
from ankihub.constants import API_URL_BASE
from aqt.utils import showText
from requests import Response, HTTPError



class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self):
        self._headers = {"Content-Type": "application/json"}
        self._config = Config()
        self._base_url = API_URL_BASE
        token = self._config.get_token()
        if token:
            self._headers["Authorization"] = f"Token {token}"

    def _call_api(self, method, endpoint, data=None, params=None):
        response = requests.request(
            method=method,
            headers=self._headers,
            url=f"{self._base_url}{endpoint}",
            json=data,
            params=params,
        )
        try:
            response.raise_for_status()
        except HTTPError:
            # TODO Add retry logic and log to Sentry.
            showText("There was an issue with your request. Please try again.")
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

    def get_deck_by_id(self, deck_id: str) -> dict:
        response = self._call_api(
            "GET",
            f"/decks/{deck_id}/",
        )
        return response.json()

    def get_note_by_anki_id(self, anki_id: str) -> dict:
        return self._call_api("GET", f"/notes/{anki_id}").json()

    def create_change_note_suggestion(
        self, change_note_suggestion: dict, note_id: int
    ) -> Response:
        return self._call_api(
            "POST", f"/notes/{note_id}/suggestion/", change_note_suggestion
        )

    def create_new_note_suggestion(
        self, new_note_suggestion: dict, deck_id: int
    ) -> Response:
        return self._call_api(
            "POST", f"/decks/{deck_id}/note-suggestion/", new_note_suggestion
        )
