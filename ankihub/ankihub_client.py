import uuid
from pathlib import Path
from typing import Dict, Iterator, List, TypedDict

import requests
from requests import PreparedRequest, Request, Response, Session
from requests.exceptions import ConnectionError
from urllib3.exceptions import HTTPError

from . import LOGGER
from .constants import API_URL_BASE, ChangeTypes


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self, hooks=None, token=None):
        if hooks is None:
            self.hooks = []
        else:
            self.hooks = hooks

        self.session = Session()
        self.session.hooks["response"] = self.hooks
        self.session.headers.update({"Content-Type": "application/json"})
        if token:
            self.session.headers["Authorization"] = f"Token {token}"

    def has_token(self) -> bool:
        return "Token" in self.session.headers.get("Authorization", "")

    def _build_request(
        self,
        method,
        endpoint,
        data=None,
        params=None,
    ) -> PreparedRequest:
        url = f"{API_URL_BASE}{endpoint}"
        request = Request(
            method=method,
            url=url,
            json=data,
            params=params,
            headers=self.session.headers,
            hooks=self.session.hooks,
        )
        prepped = request.prepare()
        return prepped

    def _send_request(
        self,
        method,
        endpoint,
        data=None,
        params=None,
        treat_404_as_error=True,
    ) -> Response:
        request = self._build_request(method, endpoint, data, params)
        request.treat_404_as_error = treat_404_as_error  # type: ignore
        try:
            response = self.session.send(request)
            self.session.close()
            return response
        except (ConnectionError, HTTPError) as e:
            LOGGER.debug(f"Connection error: {e}")
            raise ConnectionError(
                "The AnkiHub add-on was unable to connect to the internet."
            )

    def login(self, credentials: dict) -> Response:
        response = self._send_request("POST", "/login/", credentials)
        token = response.json().get("token") if response else ""
        if token:
            self.session.headers["Authorization"] = f"Token {token}"
        return response

    def signout(self):
        result = self._send_request("POST", "/logout/")
        if result and result.status_code == 204:
            self.session.headers["Authorization"] = ""

    def upload_deck(self, file: Path, anki_deck_id: int) -> Response:
        key = file.name
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            deck_data = f.read()

        s3_response = requests.put(s3_url, data=deck_data)
        if s3_response.status_code != 200:
            return s3_response

        response = self._send_request(
            "POST", "/decks/", data={"key": key, "anki_id": anki_deck_id}
        )
        return response

    def download_deck(self, deck_file_name: str) -> Response:
        presigned_url_response = self.get_presigned_url(
            key=deck_file_name, action="download"
        )
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        return requests.get(s3_url)

    def get_deck_updates(
        self, ankihub_deck_uuid: uuid.UUID, since: float
    ) -> Iterator[Response]:
        class Params(TypedDict, total=False):
            page: int
            since: str

        params: Params = {"since": str(since), "page": 1} if since else {"page": 1}
        has_next_page = True
        while has_next_page:
            response = self._send_request(
                "GET",
                f"/decks/{ankihub_deck_uuid}/updates",
                params=params,
            )
            if response.status_code == 200:
                has_next_page = response.json()["has_next"]
                params["page"] += 1
                yield response
            else:
                has_next_page = False
                yield response

    def get_deck_by_id(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/",
        )
        return response

    def get_note_by_ankihub_id(self, ankihub_note_uuid: uuid.UUID) -> Response:
        response = self._send_request("GET", f"/notes/{ankihub_note_uuid}")
        return response

    def create_change_note_suggestion(
        self,
        ankihub_note_uuid: uuid.UUID,
        fields: List[Dict],
        tags: List[str],
        change_type: ChangeTypes,
        comment: str,
    ) -> Response:
        suggestion = {
            "ankihub_id": str(ankihub_note_uuid),
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "comment": comment,
        }
        response = self._send_request(
            "POST",
            f"/notes/{ankihub_note_uuid}/suggestion/",
            data=suggestion,
        )
        return response

    def create_new_note_suggestion(
        self,
        ankihub_deck_uuid: uuid.UUID,
        ankihub_note_uuid: uuid.UUID,
        anki_note_id: int,
        fields: List[dict],
        tags: List[str],
        change_type: ChangeTypes,
        note_type_name: str,
        anki_note_type_id: int,
        comment: str,
    ) -> Response:
        suggestion = {
            "anki_id": anki_note_id,
            "ankihub_id": str(ankihub_note_uuid),
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "note_type": note_type_name,
            "note_type_id": anki_note_type_id,
            "comment": comment,
        }
        response = self._send_request(
            "POST",
            f"/decks/{ankihub_deck_uuid}/note-suggestion/",
            data=suggestion,
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
        response = self._send_request(method, endpoint, params=data)
        return response

    def get_note_type(self, anki_note_type_id: int) -> Response:
        response = self._send_request("GET", f"/note-types/{anki_note_type_id}/")
        return response

    def get_protected_fields(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-fields/",
            treat_404_as_error=False,
        )
        return response

    def get_protected_tags(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-tags/",
            treat_404_as_error=False,
        )
        return response

    def bulk_suggest_tags(
        self, ankihub_note_uuids: List[uuid.UUID], tags: List[str]
    ) -> Response:
        data = {"notes": [str(note_id) for note_id in ankihub_note_uuids], "tags": tags}
        response = self._send_request("POST", "/suggestions/bulk/", data=data)
        return response

    def upload_logs(self, file: Path, key: str) -> Response:
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            log_data = f.read()

        s3_response = requests.put(s3_url, data=log_data)
        return s3_response
