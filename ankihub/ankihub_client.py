from pathlib import Path
from typing import Dict, List

import requests
from aqt import qDebug
from aqt.utils import showText

from ankihub.config import Config
from ankihub.constants import API_URL_BASE, USER_SUPPORT_EMAIL_SLUG
from requests import Response


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self):
        self._headers = {"Content-Type": "application/json"}
        self._config = Config()
        self._base_url = API_URL_BASE
        self.token = self._config.private_config.token
        if self.token:
            self._headers["Authorization"] = f"Token {self.token}"

    def _call_api(self, method, endpoint, data=None, params=None):
        url = f"{self._base_url}{endpoint}"
        response = requests.request(
            method=method,
            headers=self._headers,
            url=url,
            json=data,
            params=params,
        )
        qDebug(f"request: {method} {url} {data} {params} {self._headers}")
        qDebug(f"response status: {response.status_code}")
        qDebug(f"response content: {response.content}")
        if response.status_code > 299:
            showText(
                "Uh oh! There was a problem with your request.\n\n"
                "If you haven't already signed in using the AnkiHub menu please do so. "
                "Make sure your username and password are correct and that you have "
                "confirmed your AnkiHub account through email verification. If you "
                "believe this is an error, please reach out to user support at "
                f"{USER_SUPPORT_EMAIL_SLUG}. This error will be automatically reported."
            )
        return response

    def login(self, credentials: dict):
        self.signout()
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
        qDebug("Token cleared from config.")

    def upload_deck(self, file: Path) -> Response:
        key = file.name
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            deck_data = f.read()
        s3_response = requests.put(s3_url, data=deck_data)
        qDebug(f"request url: {s3_response.request.url}")
        qDebug(f"response status: {s3_response.status_code}")
        qDebug(f"response content: {str(s3_response.content)}")
        response = self._call_api("POST", "/decks/", data={"key": key})
        return response

    def get_deck_updates(self, deck_id: str) -> Response:
        since = self._config.private_config.last_sync
        params = (
            {"since": f"{self._config.private_config.last_sync}", "page": 1}
            if since
            else {"page": 1}
        )
        has_next_page = True
        while has_next_page:
            response = self._call_api(
                "GET",
                f"/decks/{deck_id}/updates",
                params=params,
            )
            if response.status_code == 200:
                has_next_page = response.json()["has_next"]
                params["page"] += 1
                yield response
            else:
                has_next_page = False
                yield response

    def get_deck_by_id(self, deck_id: str) -> Response:
        response = self._call_api(
            "GET",
            f"/decks/{deck_id}/",
        )
        return response

    def get_note_by_anki_id(self, anki_id: str) -> Response:
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
            "POST", f"/notes/{ankihub_id}/suggestion/", data=suggestion
        )
        return response

    def create_new_note_suggestion(
        self,
        deck_id: int,
        anki_id: int,
        ankihub_id: str,
        fields: List[dict],
        tags: List[str],
    ) -> Response:
        # TODO include the note model name
        suggestion = {
            "related_deck": deck_id,
            "anki_id": anki_id,
            "ankihub_id": ankihub_id,
            "fields": fields,
            "tags": tags,
        }
        response = self._call_api(
            "POST", f"/decks/{deck_id}/note-suggestion/", data=suggestion
        )
        return response

    def get_presigned_url(self, key: str, action: str) -> Response:
        """
        Get URL for s3.
        :param key: deck name
        :param action: upload or download
        :return:
        """
        method = "GET"
        endpoint = "/decks/pre-signed-url"
        data = {"key": key, "type": action}
        response = self._call_api(method, endpoint, params=data)
        return response
