from pathlib import Path
from pprint import pformat
from typing import Dict, Iterator, List, TypedDict, Union

import requests
from requests import PreparedRequest, Request, Response, Session
from requests.exceptions import ConnectionError
from urllib3.exceptions import HTTPError

from . import LOGGER
from .config import config
from .constants import API_URL_BASE, ChangeTypes


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self, send_request=True, hooks=None):
        if hooks is None:
            self.hooks = []
        else:
            self.hooks = hooks

        self.send_request = send_request
        self.session = Session()
        self.session.hooks["response"] = self.hooks
        self.session.headers.update({"Content-Type": "application/json"})
        self.token = config.private_config.token
        if self.token:
            self.session.headers["Authorization"] = f"Token {self.token}"

    def _call_api(
        self, method, endpoint, data=None, params=None
    ) -> Union[PreparedRequest, Response]:
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
        if self.send_request is False:
            return prepped
        else:
            try:
                response = self.session.send(prepped)
                self.session.close()
                return response
            except (ConnectionError, HTTPError) as e:
                LOGGER.debug(f"Connection error: {e}")
                # TODO collect suggestion requests and retry later?
                pass

    def login(self, credentials: dict) -> Response:
        response = self._call_api("POST", "/login/", credentials)
        token = response.json().get("token")
        if token:
            self.session.headers["Authorization"] = f"Token {token}"
        return response

    def signout(self):
        result = self._call_api("POST", "/logout/")
        if isinstance(result, Response) and result.status_code == 204:
            config.save_token("")
            self.session.headers["Authorization"] = ""
            LOGGER.debug("Token cleared from config.")

    def upload_deck(self, file: Path, anki_id: int) -> Response:
        key = file.name
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            deck_data = f.read()
        s3_response = requests.put(s3_url, data=deck_data)
        LOGGER.debug(f"request url: {s3_response.request.url}")
        LOGGER.debug(f"response status: {s3_response.status_code}")
        if s3_response.status_code not in [500, 404]:
            LOGGER.debug(f"response content: {pformat(s3_response.content)}")
        response = self._call_api(
            "POST", "/decks/", data={"key": key, "anki_id": anki_id}
        )
        return response

    def get_deck_updates(self, deck_id: str) -> Iterator[Response]:
        since = config.private_config.last_sync

        class Params(TypedDict, total=False):
            page: int
            since: str

        params: Params = (
            {"since": f"{config.private_config.last_sync}", "page": 1}
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
                # assert type(params["page"]) == int
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

    def get_note_by_anki_id(self, anki_id: int) -> Response:
        response = self._call_api("GET", f"/notes/{anki_id}")
        return response

    def create_change_note_suggestion(
        self,
        ankihub_note_uuid: str,
        fields: List[Dict],
        tags: List[str],
        change_type: ChangeTypes,
        comment: str,
    ) -> Response:
        suggestion = {
            "ankihub_id": ankihub_note_uuid,
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "comment": comment,
        }
        response = self._call_api(
            "POST", f"/notes/{ankihub_note_uuid}/suggestion/", data=suggestion
        )
        return response

    def create_new_note_suggestion(
        self,
        ankihub_deck_uuid: str,
        ankihub_note_uuid: str,
        anki_id: int,
        fields: List[dict],
        tags: List[str],
        change_type: ChangeTypes,
        comment: str,
    ) -> Response:
        # TODO include the note model name
        suggestion = {
            "anki_id": anki_id,
            "ankihub_id": ankihub_note_uuid,
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "comment": comment,
        }
        response = self._call_api(
            "POST", f"/decks/{ankihub_deck_uuid}/note-suggestion/", data=suggestion
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
