import csv
import dataclasses
import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, TypedDict

import requests
from requests import PreparedRequest, Request, Response, Session
from requests.exceptions import HTTPError

from .lib import dataclasses_json  # type: ignore
from .lib.dataclasses_json import DataClassJsonMixin  # type: ignore

LOGGER = logging.getLogger(__name__)

API_URL_BASE = "https://app.ankihub.net/api"

DECK_UPDATE_PAGE_SIZE = 2000  # seems to work well in terms of speed

CSV_DELIMITER = ";"


# TODO Make sure these match up with SuggestionType.choices on AnkiHub
class SuggestionType(Enum):
    UPDATED_CONTENT = "updated_content", "Updated content"
    NEW_CONTENT = "new_content", "New content"
    SPELLING_GRAMMATICAL = "spelling/grammar", "Spelling/Grammar"
    CONTENT_ERROR = "content_error", "Content error"
    NEW_CARD_TO_ADD = "new_card_to_add", "New card to add"
    NEW_TAGS = "new_tags", "New Tags"
    UPDATED_TAGS = "updated_tags", "Updated Tags"
    OTHER = "other", "Other"


def suggestion_type_from_str(s: str) -> Optional[SuggestionType]:
    if s in ["original_content", "new_note", None]:
        return None

    result = next(x for x in SuggestionType if x.value[0] == s)
    return result


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
    last_update_type: Optional[SuggestionType] = dataclasses.field(
        metadata=dataclasses_json.config(
            encoder=lambda x: x.value[0],
            decoder=suggestion_type_from_str,
        ),
        default=None,
    )


@dataclass
class DeckUpdateChunk(DataClassJsonMixin):
    latest_update: str
    protected_fields: Dict[int, List[str]]
    protected_tags: List[str]
    notes: List[NoteUpdate]


@dataclass
class DeckInfo(DataClassJsonMixin):
    ankihub_deck_uuid: uuid.UUID = dataclasses.field(
        metadata=dataclasses_json.config(field_name="id")
    )
    owner: bool = dataclasses.field(
        metadata=dataclasses_json.config(
            encoder=lambda b: 1 if b else 0,
            decoder=lambda i: bool(i),
        )
    )
    anki_did: int = dataclasses.field(
        metadata=dataclasses_json.config(field_name="anki_id")
    )
    name: str
    csv_last_upload: str
    csv_notes_filename: str


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

    def upload_deck(self, file: Path, anki_deck_id: int, private: bool) -> uuid.UUID:
        key = file.name
        s3_url = self.get_presigned_url(key=key, action="upload")
        with open(file, "rb") as f:
            deck_data = f.read()

        s3_response = requests.put(s3_url, data=deck_data)
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

        response = self._send_request(
            "POST",
            "/decks/",
            data={"key": key, "anki_id": anki_deck_id, "is_private": private},
        )
        if response.status_code != 201:
            raise AnkiHubRequestError(response)

        data = response.json()
        ankihub_did = uuid.UUID(data["deck_id"])
        return ankihub_did

    def download_deck(
        self,
        ankihub_deck_uuid: uuid.UUID,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> List[NoteUpdate]:
        deck_info = self.get_deck_by_id(ankihub_deck_uuid)

        s3_url = self.get_presigned_url(
            key=deck_info.csv_notes_filename, action="download"
        )

        if download_progress_cb:
            s3_response_content = self._download_with_progress_cb(
                s3_url, download_progress_cb
            )
        else:
            s3_response = requests.get(s3_url)
            if s3_response.status_code != 200:
                raise AnkiHubRequestError(s3_response)
            s3_response_content = s3_response.content

        deck_csv_content = s3_response_content.decode("utf-8")
        reader = csv.DictReader(
            deck_csv_content.splitlines(), delimiter=CSV_DELIMITER, quotechar="'"
        )
        # TODO Validate .csv
        notes_data_raw = [row for row in reader]
        notes_data_raw = transform_notes_data(notes_data_raw)
        notes_data = [NoteUpdate.from_dict(row) for row in notes_data_raw]

        return notes_data

    def _download_with_progress_cb(
        self, url: str, progress_cb: Callable[[int], None]
    ) -> bytes:
        with requests.get(url, stream=True) as response:
            if response.status_code != 200:
                raise AnkiHubRequestError(response)

            total_size = int(response.headers.get("content-length"))
            if total_size == 0:
                return response.content

            content = b""
            chunk_size = int(min(total_size * 0.05, 10**6))
            prev_percent = 0
            for i, chunk in enumerate(response.iter_content(chunk_size=chunk_size)):
                percent = int(i * chunk_size / total_size * 100)
                if chunk:
                    content += chunk
                    if percent != prev_percent:
                        progress_cb(percent)
                        prev_percent = percent
        return content

    def get_deck_updates(
        self,
        ankihub_deck_uuid: uuid.UUID,
        since: str,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> Iterator[DeckUpdateChunk]:
        class Params(TypedDict, total=False):
            page: int
            since: str
            size: int

        params: Params = {
            "since": str(since) if since else None,
            "page": 1,
            "size": DECK_UPDATE_PAGE_SIZE,
        }
        has_next_page = True
        i = 0
        prev_percent = 0
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

            i += 1

            if download_progress_cb:
                total = data["total"]
                if total == 0:
                    percent = 100
                else:
                    percent = int(i * DECK_UPDATE_PAGE_SIZE / total * 100)
                if percent != prev_percent:
                    download_progress_cb(percent)
                    prev_percent = percent

    def get_deck_by_id(self, ankihub_deck_uuid: uuid.UUID) -> DeckInfo:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        result = DeckInfo.from_dict(data)
        return result

    def create_change_note_suggestion(
        self,
        ankihub_note_uuid: uuid.UUID,
        fields: List[Dict],
        tags: List[str],
        change_type: SuggestionType,
        comment: str,
    ) -> None:
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

    def create_new_note_suggestion(
        self,
        ankihub_deck_uuid: uuid.UUID,
        ankihub_note_uuid: uuid.UUID,
        anki_note_id: int,
        fields: List[dict],
        tags: List[str],
        note_type_name: str,
        anki_note_type_id: int,
        comment: str,
    ):
        suggestion = {
            "anki_id": anki_note_id,
            "ankihub_id": str(ankihub_note_uuid),
            "fields": fields,
            "tags": tags,
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

    def get_presigned_url(self, key: str, action: str) -> str:
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

        result = response.json()["pre_signed_url"]
        return result

    def get_note_type(self, anki_note_type_id: int) -> Dict[str, Any]:
        response = self._send_request("GET", f"/note-types/{anki_note_type_id}/")
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        result = to_anki_note_type(data)
        return result

    def get_protected_fields(
        self, ankihub_deck_uuid: uuid.UUID
    ) -> Dict[int, List[str]]:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-fields/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        protected_fields_raw = response.json()["fields"]
        result = {
            int(field_id): field_names
            for field_id, field_names in protected_fields_raw.items()
        }
        return result

    def get_protected_tags(self, ankihub_deck_uuid: uuid.UUID) -> List[str]:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/protected-tags/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        result = response.json()["tags"]
        result = [x for x in result if x.strip()]
        return result

    def bulk_suggest_tags(
        self, ankihub_note_uuids: List[uuid.UUID], tags: List[str]
    ) -> None:
        data = {"notes": [str(note_id) for note_id in ankihub_note_uuids], "tags": tags}
        response = self._send_request("POST", "/suggestions/bulk/", data=data)
        if response.status_code != 201:
            raise AnkiHubRequestError(response)


def transform_notes_data(notes_data: List[Dict]) -> List[Dict]:
    # TODO Fix differences between csv (used when installing for the first time) vs.
    # json in responses (used when getting updates).
    # For example for one a field is named "note_id" and for the other "id"
    result = [
        {
            **note_data,
            "fields": json.loads(note_data["fields"])
            if isinstance(note_data["fields"], str)
            else note_data["fields"],
            "anki_id": int((note_data["anki_id"])),
            "note_id": note_data.get(
                "note_id", note_data.get("ankihub_id", note_data.get("id"))
            ),
            "note_type_id": int(note_data["note_type_id"]),
            "tags": json.loads(note_data["tags"])
            if isinstance(note_data["tags"], str)
            else note_data["tags"],
        }
        for note_data in notes_data
    ]
    return result


def to_anki_note_type(note_type_data: Dict) -> Dict[str, Any]:
    """Turn JSON response from AnkiHubClient.get_note_type into NotetypeDict."""
    del note_type_data["anki_id"]
    note_type_data["tmpls"] = note_type_data.pop("templates")
    note_type_data["flds"] = note_type_data.pop("fields")
    return note_type_data
