import csv
import dataclasses
import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, TypedDict

import dataclasses_json
import requests
from dataclasses_json import DataClassJsonMixin
from requests import PreparedRequest, Request, Response, Session
from requests.exceptions import HTTPError

from . import LOGGER
from .constants import API_URL_BASE, ChangeTypes

CSV_DELIMITER = ";"


@dataclass
class FieldUpdate(DataClassJsonMixin):
    name: str
    order: int
    value: str


@dataclass
class NoteUpdate(DataClassJsonMixin):
    fields: List[FieldUpdate]
    ankihub_note_uuid: uuid.UUID = dataclasses.field(
        metadata=dataclasses_json.config(field_name="note_id")
    )
    anki_nid: int = dataclasses.field(
        metadata=dataclasses_json.config(field_name="anki_id")
    )
    mid: int = dataclasses.field(
        metadata=dataclasses_json.config(field_name="note_type_id")
    )
    tags: List[str]


@dataclass
class DeckUpdateChunk(DataClassJsonMixin):
    latest_update: str
    protected_fields: Dict[int, List[str]]
    protected_tags: List[str]
    notes: List[NoteUpdate]


class AnkiHubRequestError(Exception):
    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        return (
            f"AnkiHub request error: {self.response.status_code} {self.response.reason}"
        )


def http_error_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin http error hook.")

    try:
        response.raise_for_status()
    except HTTPError:
        LOGGER.debug("http error hook raises AnkiHubRequestError.")
        raise AnkiHubRequestError(response)

    return response


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self, hooks=None, token=None):
        self.hooks = [http_error_hook]
        if hooks is not None:
            self.hooks += hooks

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
    ) -> Response:
        request = self._build_request(method, endpoint, data, params)
        response = self.session.send(request)
        self.session.close()
        return response

    def login(self, credentials: dict) -> str:
        response = self._send_request("POST", "/login/", credentials)
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        token = response.json().get("token") if response else ""
        if token:
            self.session.headers["Authorization"] = f"Token {token}"

        data = response.json()
        token = data.get("token")
        return token

    def signout(self):
        response = self._send_request("POST", "/logout/")
        if response and response.status_code == 204:
            self.session.headers["Authorization"] = ""
        else:
            raise AnkiHubRequestError(response)

    def upload_deck(self, file: Path, anki_deck_id: int) -> uuid.UUID:
        key = file.name
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        if presigned_url_response.status_code != 200:
            raise AnkiHubRequestError(presigned_url_response)

        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            deck_data = f.read()

        s3_response = requests.put(s3_url, data=deck_data)
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

        response = self._send_request(
            "POST", "/decks/", data={"key": key, "anki_id": anki_deck_id}
        )
        if response.status_code != 201:
            raise AnkiHubRequestError(response)

        data = response.json()
        ankihub_did = uuid.UUID(data["deck_id"])
        return ankihub_did

    def download_deck(self, deck_file_name: str) -> List[NoteUpdate]:
        presigned_url_response = self.get_presigned_url(
            key=deck_file_name, action="download"
        )
        if presigned_url_response.status_code != 200:
            raise AnkiHubRequestError(presigned_url_response)

        s3_url = presigned_url_response.json()["pre_signed_url"]
        s3_response = requests.get(s3_url)
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

        deck_csv_content = s3_response.content
        out_file = Path(tempfile.mkdtemp()) / f"{deck_file_name}"
        with out_file.open("wb") as f:
            f.write(deck_csv_content)
            LOGGER.debug(f"Wrote {deck_file_name} to {out_file}")
            # TODO Validate .csv

        with out_file.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=CSV_DELIMITER, quotechar="'")
            notes_data_raw = [row for row in reader]
            notes_data_raw = transform_notes_data(notes_data_raw)
            notes_data = [NoteUpdate.from_dict(row) for row in notes_data_raw]

        return notes_data

    def get_deck_updates(
        self, ankihub_deck_uuid: uuid.UUID, since: str
    ) -> Iterator[DeckUpdateChunk]:
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
            if response.status_code != 200:
                raise AnkiHubRequestError(response)

            data = response.json()
            has_next_page = data["has_next"]
            params["page"] += 1

            data["notes"] = transform_notes_data(data["notes"])
            note_updates = DeckUpdateChunk.from_dict(data)
            yield note_updates

    def get_deck_by_id(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
        return response

    def get_note_by_ankihub_id(self, ankihub_note_uuid: uuid.UUID) -> Response:
        response = self._send_request("GET", f"/notes/{ankihub_note_uuid}")
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
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
        if response.status_code != 201:
            raise AnkiHubRequestError(response)
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
        if response.status_code != 201:
            raise AnkiHubRequestError(response)
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
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
        return response

    def get_note_type(self, anki_note_type_id: int) -> Response:
        response = self._send_request("GET", f"/note-types/{anki_note_type_id}/")
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
        return response

    def get_protected_fields(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-fields/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
        return response

    def get_protected_tags(self, ankihub_deck_uuid: uuid.UUID) -> Response:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-tags/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)
        return response

    def bulk_suggest_tags(
        self, ankihub_note_uuids: List[uuid.UUID], tags: List[str]
    ) -> Response:
        data = {"notes": [str(note_id) for note_id in ankihub_note_uuids], "tags": tags}
        response = self._send_request("POST", "/suggestions/bulk/", data=data)
        if response.status_code != 201:
            raise AnkiHubRequestError(response)
        return response


def transform_notes_data(notes_data: List[Dict]) -> List[Dict]:
    # TODO fix differences between csv when installing for the first time vs. when updating
    # on the AnkiHub side
    # for example for one the fields name is "note_id" and for the other "id"
    result = [
        {
            **note_data,
            "anki_id": int((note_data["anki_id"])),
            "note_id": note_data.get(
                "note_id", note_data.get("ankihub_id", note_data.get("id"))
            ),
            "fields": json.loads(note_data["fields"])
            if isinstance(note_data["fields"], str)
            else note_data["fields"],
            "tags": json.loads(note_data["tags"])
            if isinstance(note_data["tags"], str)
            else note_data["tags"],
            "note_type_id": int(note_data["note_type_id"]),
        }
        for note_data in notes_data
    ]
    return result
