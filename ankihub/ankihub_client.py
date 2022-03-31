from typing import Union, Dict, List

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
        self.token = self._config.get_token()
        if self.token:
            self._headers["Authorization"] = f"Token {self.token}"

    def _call_api(self, method, endpoint, data=None, params=None):
        response = requests.request(
            method=method,
            headers=self._headers,
            url=f"{self._base_url}{endpoint}",
            json=data,
            params=params,
        )
        return response

    def login(self, credentials: dict):
        response = self._call_api("POST", "/login/", credentials)
        token = response.json().get("token")
        if token:
            self._config.save_token(token)
            self._headers["Authorization"] = f"Token {token}"
        self._config.save_user_email(credentials["username"])
        return response

    def signout(self):
        self._config.save_token("")
        self._headers["Authorization"] = ""

    def upload_deck(self, key: str) -> Response:
        response = self._call_api("POST", "/decks/", data={"key": key})
        return response

    def get_deck_updates(self, deck_id: str) -> Union[Response, dict]:
        response = self._call_api(
            "GET",
            f"/decks/{deck_id}/updates",
            params={"since": f"{self._config.get_last_sync()}"},
        )
        if response.status_code == 200:
            self._config.save_last_sync()
        return response

    def get_deck_by_id(self, deck_id: str) -> Union[Response, dict]:
        response = self._call_api(
            "GET",
            f"/decks/{deck_id}/",
        )
        return response

    def get_note_by_anki_id(self, anki_id: str) -> Union[Response, dict]:
        response = self._call_api("GET", f"/notes/{anki_id}")
        return response

    def create_change_note_suggestion(
        self,
        ankihub_id: str,
        fields: List[Dict],
        tags: List[str],
    ) -> Response:
        suggestion = {
            "ankihub_id": ankihub_id,
            "fields": fields,
            "tags": tags,
        }
        response = self._call_api(
            "POST", f"/notes/{ankihub_id}/suggestion/", suggestion
        )
        return response

    def create_new_note_suggestion(
        self,
        deck_id: int,
        ankihub_id: str,
        fields: Dict[str, str],
        tags: List[str],
    ) -> Response:
        suggestion = {
            "related_deck": deck_id,
            "ankihub_id": ankihub_id,
            "fields": fields,
            "tags": tags,
        }
        response = self._call_api(
            "POST", f"/decks/{deck_id}/note-suggestion/", suggestion
        )
        return response
