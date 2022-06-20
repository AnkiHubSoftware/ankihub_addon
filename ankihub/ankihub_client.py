from pathlib import Path
from typing import Dict, Iterator, List, TypedDict, Union

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

    def _call_api_or_prep_request(
        self,
        method,
        endpoint,
        data=None,
        params=None,
        send_request=True,
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
        if send_request is False:
            return prepped
        else:
            try:
                response = self.session.send(prepped)
                self.session.close()
                return response
            except (ConnectionError, HTTPError) as e:
                LOGGER.debug(f"Connection error: {e}")
                # TODO collect suggestion requests and retry later?
                raise e

    def _call_api(
        self,
        method,
        endpoint,
        data=None,
        params=None,
    ) -> Response:
        result = self._call_api_or_prep_request(
            method, endpoint, data, params, send_request=True
        )
        assert type(result) == Response
        return result

    def login(self, credentials: dict) -> Response:
        response = self._call_api("POST", "/login/", credentials)
        token = response.json().get("token")
        if token:
            self.session.headers["Authorization"] = f"Token {token}"
        return response

    def signout(self):
        result = self._call_api("POST", "/logout/")
        if result.status_code == 204:
            self.session.headers["Authorization"] = ""

    def upload_deck(self, file: Path, anki_id: int) -> Response:
        key = file.name
        presigned_url_response = self._get_presigned_url(key=key, action="upload")
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            deck_data = f.read()

        s3_response = requests.put(s3_url, data=deck_data)
        if s3_response.status_code != 200:
            return s3_response

        response = self._call_api(
            "POST", "/decks/", data={"key": key, "anki_id": anki_id}
        )
        return response

    def download_deck(self, deck_file_name: str) -> Response:
        presigned_url_response = self._get_presigned_url(
            key=deck_file_name, action="download"
        )
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        return requests.get(s3_url)

    def get_deck_updates(self, deck_id: str, since: float) -> Iterator[Response]:
        class Params(TypedDict, total=False):
            page: int
            since: str

        params: Params = {"since": str(since), "page": 1} if since else {"page": 1}
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
        result = self._create_change_note_suggestion(
            ankihub_note_uuid=ankihub_note_uuid,
            fields=fields,
            tags=tags,
            change_type=change_type,
            comment=comment,
        )
        assert type(result) == Response
        return result

    def _create_change_note_suggestion(
        self,
        ankihub_note_uuid: str,
        fields: List[Dict],
        tags: List[str],
        change_type: ChangeTypes,
        comment: str,
        send_request=True,
    ) -> Union[Response, PreparedRequest]:
        suggestion = {
            "ankihub_id": ankihub_note_uuid,
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "comment": comment,
        }
        response = self._call_api_or_prep_request(
            "POST",
            f"/notes/{ankihub_note_uuid}/suggestion/",
            data=suggestion,
            send_request=send_request,
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
        send_request=True,
    ) -> Response:
        result = self._create_new_note_suggestion(
            ankihub_deck_uuid=ankihub_deck_uuid,
            ankihub_note_uuid=ankihub_note_uuid,
            anki_id=anki_id,
            fields=fields,
            tags=tags,
            change_type=change_type,
            comment=comment,
            send_request=True,
        )
        assert type(result) == Response
        return result

    def _create_new_note_suggestion(
        self,
        ankihub_deck_uuid: str,
        ankihub_note_uuid: str,
        anki_id: int,
        fields: List[dict],
        tags: List[str],
        change_type: ChangeTypes,
        comment: str,
        send_request=True,
    ) -> Union[Response, PreparedRequest]:
        # TODO include the note model name
        suggestion = {
            "anki_id": anki_id,
            "ankihub_id": ankihub_note_uuid,
            "fields": fields,
            "tags": tags,
            "change_type": change_type.value[0],
            "comment": comment,
        }
        response = self._call_api_or_prep_request(
            "POST",
            f"/decks/{ankihub_deck_uuid}/note-suggestion/",
            data=suggestion,
            send_request=send_request,
        )
        return response

    def _get_presigned_url(self, key: str, action: str) -> Response:
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
