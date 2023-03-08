import csv
import dataclasses
import gzip
import json
import logging
import os
import re
import uuid
from abc import ABC
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from io import BufferedReader
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    TypedDict,
    Union,
)

import requests
from requests import PreparedRequest, Request, Response, Session

from mashumaro import field_options
from mashumaro.config import BaseConfig
from mashumaro.mixins.json import DataClassJSONMixin

LOGGER = logging.getLogger(__name__)

S3_BUCKET_URL = (
    "https://ankihubbucket.s3.us-east-2.amazonaws.com"
    if bool(os.getenv("DEVELOPMENT", True))
    else "https://ankihub-decks-assets.s3.amazonaws.com/"
)

API_URL_BASE = "https://app.ankihub.net/api"
API_VERSION = 7.0

DECK_UPDATE_PAGE_SIZE = 2000  # seems to work well in terms of speed
DECK_EXTENSION_UPDATE_PAGE_SIZE = 2000

CSV_DELIMITER = ";"

ANKIHUB_DATETIME_FORMAT_STR = "%Y-%m-%dT%H:%M:%S.%f%z"


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

    result = next((x for x in SuggestionType if x.value[0] == s), None)
    if result is None:
        raise ValueError("Invalid suggestion type string")
    return result


class DataClassJSONMixinWithConfig(DataClassJSONMixin):
    class Config(BaseConfig):
        serialize_by_alias = True


@dataclass
class Field(DataClassJSONMixinWithConfig):
    name: str
    order: int
    value: str


@dataclass
class NoteInfo(DataClassJSONMixinWithConfig):
    ankihub_note_uuid: uuid.UUID = dataclasses.field(
        metadata=field_options(alias="note_id")
    )
    anki_nid: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    mid: int = dataclasses.field(metadata=field_options(alias="note_type_id"))
    fields: List[Field]
    tags: Optional[List[str]]  # None means no tag changes
    guid: str
    last_update_type: Optional[SuggestionType] = dataclasses.field(
        metadata=field_options(
            serialize=lambda x: x.value[0] if x is not None else None,
            deserialize=suggestion_type_from_str,
        ),
        default=None,
    )


@dataclass
class NoteInfoForUpload(DataClassJSONMixinWithConfig):
    ankihub_note_uuid_str: str = dataclasses.field(metadata=field_options(alias="id"))
    anki_nid: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    mid: int = dataclasses.field(metadata=field_options(alias="note_type_id"))
    fields: List[Field]
    tags: List[str]
    guid: str


def note_info_for_upload(note_info: NoteInfo) -> NoteInfoForUpload:
    return NoteInfoForUpload(
        ankihub_note_uuid_str=str(note_info.ankihub_note_uuid),
        anki_nid=note_info.anki_nid,
        mid=note_info.mid,
        fields=note_info.fields,
        tags=note_info.tags,
        guid=note_info.guid,
    )


@dataclass
class DeckUpdateChunk(DataClassJSONMixinWithConfig):
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
            if x
            else None,
        ),
    )
    protected_fields: Dict[int, List[str]]
    protected_tags: List[str]
    notes: List[NoteInfo]


@dataclass
class Deck(DataClassJSONMixinWithConfig):
    ankihub_deck_uuid: uuid.UUID = dataclasses.field(metadata=field_options(alias="id"))
    owner: bool = dataclasses.field(
        metadata=field_options(
            serialize=lambda b: 1 if b else 0,
            deserialize=lambda i: bool(i),
        )
    )
    anki_did: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    name: str
    csv_last_upload: datetime = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
            if x
            else None
        )
    )
    csv_notes_filename: str


@dataclass
class NoteSuggestion(DataClassJSONMixinWithConfig, ABC):
    ankihub_note_uuid: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="ankihub_id",
            serialize=str,
        ),
    )
    anki_nid: int = dataclasses.field(
        metadata=field_options(
            alias="anki_id",
        )
    )
    fields: List[Field]
    comment: str


@dataclass
class ChangeNoteSuggestion(NoteSuggestion):
    added_tags: List[str]
    removed_tags: List[str]
    change_type: SuggestionType = dataclasses.field(
        metadata=field_options(
            serialize=lambda x: x.value[0],
            deserialize=suggestion_type_from_str,
        ),
    )

    def __post_serialize__(self, d: Dict[Any, Any]) -> Dict[Any, Any]:
        # note_id is needed for bulk change note suggestions
        d["note_id"] = d["ankihub_id"]
        return d


@dataclass
class NewNoteSuggestion(NoteSuggestion):
    ankihub_deck_uuid: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="deck_id",
            serialize=str,
        ),
    )
    note_type_name: str = dataclasses.field(
        metadata=field_options(
            alias="note_type",
        )
    )
    anki_note_type_id: int = dataclasses.field(
        metadata=field_options(
            alias="note_type_id",
        )
    )
    tags: Optional[List[str]]  # None means no tag changes
    guid: str


@dataclass
class TagGroupValidationResponse(DataClassJSONMixinWithConfig):
    tag_group_name: str
    success: bool
    deck_extension_id: Optional[int]
    errors: List[str]


@dataclass
class OptionalTagSuggestion(DataClassJSONMixinWithConfig):
    tag_group_name: str
    deck_extension_id: int
    ankihub_note_uuid: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="related_note",
            serialize=str,
        ),
    )
    tags: List[str]


class AnkiHubRequestError(Exception):
    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        return (
            f"AnkiHub request error: {self.response.status_code} {self.response.reason}"
        )


@dataclass
class DeckExtension(DataClassJSONMixinWithConfig):
    id: int
    ankihub_deck_uuid: uuid.UUID = dataclasses.field(
        metadata=field_options(alias="deck")
    )
    owner_id: int = dataclasses.field(metadata=field_options(alias="owner"))
    name: str
    tag_group_name: str
    description: str


@dataclass
class NoteCustomization(DataClassJSONMixinWithConfig):
    ankihub_nid: uuid.UUID = dataclasses.field(metadata=field_options(alias="note"))
    tags: List[str]


@dataclass
class DeckExtensionUpdateChunk(DataClassJSONMixinWithConfig):
    note_customizations: List[NoteCustomization]
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
            if x
            else None,
        ),
        default=None,
    )


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self, hooks=None, token=None, local_media_dir_path=None):
        self.session = Session()
        self.local_media_dir_path = local_media_dir_path

        if hooks is not None:
            self.session.hooks["response"] = hooks

        self.session.headers.update({"Content-Type": "application/json"})
        self.session.headers.update(
            {"Accept": f"application/json; version={API_VERSION}"}
        )
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
        self.session.headers["Authorization"] = ""
        response = self._send_request("POST", "/logout/")
        if response.status_code not in [204, 401]:
            raise AnkiHubRequestError(response)

    def upload_deck(
        self,
        deck_name: str,
        notes_data: List[NoteInfo],
        note_types_data: List[Dict],
        anki_deck_id: int,
        private: bool,
    ) -> uuid.UUID:
        deck_name_normalized = re.sub('[\\\\/?<>:*|"^]', "_", deck_name)
        deck_file_name = f"{deck_name_normalized}-{uuid.uuid4()}.json.gz"

        s3_url = self.get_presigned_url(key=deck_file_name, action="upload")

        notes_data_transformed = [
            note_info_for_upload(note_data).to_dict() for note_data in notes_data
        ]
        data = self._gzip_compress_string(
            json.dumps(
                {
                    "notes": notes_data_transformed,
                    "note_types": note_types_data,
                }
            ),
        )

        self._upload_to_s3(s3_url, data)

        response = self._send_request(
            "POST",
            "/decks/",
            data={
                "key": deck_file_name,
                "name": deck_name,
                "anki_id": anki_deck_id,
                "is_private": private,
            },
        )
        if response.status_code != 201:
            raise AnkiHubRequestError(response)

        response_data = response.json()
        ankihub_did = uuid.UUID(response_data["deck_id"])
        return ankihub_did

    def _upload_to_s3(self, s3_url: str, data: Union[bytes, BufferedReader]) -> None:
        s3_response = requests.put(
            s3_url,
            data=data,
        )
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

    def upload_images(self, image_paths: List[Path], bucket_path: str) -> None:
        # TODO: send all images at once instad of looping through each one
        for image_path in image_paths:
            key = f"{bucket_path}/{image_path.name}"
            s3_url = self.get_presigned_url(key=key, action="upload")
            with open(image_path, "rb") as image_file:
                self._upload_to_s3(s3_url, image_file)

    def download_images(self, img_names: List[str], deck_id: uuid.UUID) -> None:
        deck_images_remote_dir = f"{S3_BUCKET_URL}/deck_images/{deck_id}/notes/"

        for img_name in img_names:
            img_path = self.local_media_dir_path / img_name
            # First we check if the image already exists.
            # If yes, we skip this iteration.
            if os.path.isfile(img_path):
                continue

            # If not, download the image from bucket
            # and store the image locally
            img_remote_path = deck_images_remote_dir + img_name
            response = requests.get(img_remote_path, stream=True)

            # Log and skip this iteration if the response is not 200 OK
            if not response.ok:
                LOGGER.info(
                    f"Unable to download image [{img_remote_path}]. Response status code: {response.status_code}"
                )
                continue

            # If we get a valid response, open the file and write the content
            with open(img_path, "wb") as handle:
                for block in response.iter_content(1024):
                    if not block:
                        break

                    handle.write(block)

    def _gzip_compress_string(self, string: str) -> bytes:
        result = gzip.compress(
            bytes(
                string,
                encoding="utf-8",
            )
        )
        return result

    def download_deck(
        self,
        ankihub_deck_uuid: uuid.UUID,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> List[NoteInfo]:
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

        if deck_info.csv_notes_filename.endswith(".gz"):
            deck_csv_content = gzip.decompress(s3_response_content).decode("utf-8")
        else:
            deck_csv_content = s3_response_content.decode("utf-8")

        reader = csv.DictReader(
            deck_csv_content.splitlines(), delimiter=CSV_DELIMITER, quotechar="'"
        )
        # TODO Validate .csv
        notes_data_raw = [row for row in reader]
        notes_data_raw = transform_notes_data(notes_data_raw)
        notes_data = [NoteInfo.from_dict(row) for row in notes_data_raw]

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
            for i, chunk in enumerate(
                response.iter_content(chunk_size=chunk_size), start=1
            ):
                if chunk:
                    percent = int(i * chunk_size / total_size * 100)
                    progress_cb(percent)
                    content += chunk
        return content

    def get_deck_updates(
        self,
        ankihub_deck_uuid: uuid.UUID,
        since: datetime,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> Iterator[DeckUpdateChunk]:
        # download_progress_cb gets passed the number of notes downloaded until now

        class Params(TypedDict, total=False):
            since: str
            size: int

        params: Params = {
            "since": since.strftime(ANKIHUB_DATETIME_FORMAT_STR) if since else None,
            "size": DECK_UPDATE_PAGE_SIZE,
        }
        url = f"/decks/{ankihub_deck_uuid}/updates"
        i = 0
        notes_count = 0
        while url is not None:
            response = self._send_request(
                "GET",
                url,
                params=params if i == 0 else None,
            )
            if response.status_code != 200:
                raise AnkiHubRequestError(response)

            data = response.json()
            url = data["next"].split("/api", maxsplit=1)[1] if data["next"] else None

            data["notes"] = transform_notes_data(data["notes"])
            note_updates = DeckUpdateChunk.from_dict(data)
            yield note_updates

            i += 1
            notes_count += len(note_updates.notes)

            if download_progress_cb:
                download_progress_cb(notes_count)

    def get_deck_by_id(self, ankihub_deck_uuid: uuid.UUID) -> Deck:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        result = Deck.from_dict(data)
        return result

    def get_note_by_id(self, ankihub_note_uuid: uuid.UUID) -> NoteInfo:
        response = self._send_request(
            "GET",
            f"/notes/{ankihub_note_uuid}",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        result = NoteInfo.from_dict(data)
        return result

    def create_change_note_suggestion(
        self,
        change_note_suggestion: ChangeNoteSuggestion,
        auto_accept: bool = False,
    ) -> None:
        response = self._send_request(
            "POST",
            f"/notes/{change_note_suggestion.ankihub_note_uuid}/suggestion/",
            data={**change_note_suggestion.to_dict(), "auto_accept": auto_accept},
        )
        if response.status_code != 201:
            raise AnkiHubRequestError(response)

    def create_new_note_suggestion(
        self,
        new_note_suggestion: NewNoteSuggestion,
        auto_accept: bool = False,
    ):
        response = self._send_request(
            "POST",
            f"/decks/{new_note_suggestion.ankihub_deck_uuid}/note-suggestion/",
            data={**new_note_suggestion.to_dict(), "auto_accept": auto_accept},
        )
        if response.status_code != 201:
            raise AnkiHubRequestError(response)

    def create_suggestions_in_bulk(
        self,
        new_note_suggestions: List[NewNoteSuggestion] = [],
        change_note_suggestions: List[ChangeNoteSuggestion] = [],
        auto_accept: bool = False,
    ) -> Dict[int, Dict[str, List[str]]]:
        # returns a dict of errors by anki_nid

        errors_for_change_suggestions = self._create_suggestion_in_bulk_inner(
            suggestions=change_note_suggestions,
            url="/notes/bulk-change-suggestions/",
            auto_accept=auto_accept,
        )

        errors_for_new_note_suggestions = self._create_suggestion_in_bulk_inner(
            suggestions=new_note_suggestions,
            url="/notes/bulk-new-note-suggestions/",
            auto_accept=auto_accept,
        )

        return {
            **errors_for_change_suggestions,
            **errors_for_new_note_suggestions,
        }

    def _create_suggestion_in_bulk_inner(
        self, suggestions: Sequence[NoteSuggestion], url: str, auto_accept: bool
    ) -> Dict[int, Dict[str, List[str]]]:
        if not suggestions:
            return {}

        response = self._send_request(
            "POST",
            endpoint=url,
            data={
                "suggestions": [d.to_dict() for d in suggestions],
                "auto_accept": auto_accept,
            },
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        errors_by_anki_nid = {
            suggestion.anki_nid: d["validation_errors"]
            for d, suggestion in zip(data, suggestions)
            if d.get("validation_errors")
        }
        return errors_by_anki_nid

    def get_presigned_url(self, key: str, action: str) -> str:
        """
        Get URL for s3.
        :param key: deck name
        :param action: upload or download
        :return:
        """
        method = "GET"
        endpoint = "/decks/generate-presigned-url"
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
        if response.status_code == 404:
            return {}
        elif response.status_code != 200:
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
        if response.status_code == 404:
            return []
        elif response.status_code != 200:
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

    def get_deck_extensions_by_deck_id(self, deck_id: uuid.UUID) -> List[DeckExtension]:
        response = self._send_request(
            "GET", "/users/deck_extensions", params={"deck_id": deck_id}
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        extension_dicts = data.get("deck_extensions", [])
        result = [DeckExtension.from_dict(d) for d in extension_dicts]
        return result

    def get_deck_extension_updates(
        self,
        deck_extension_id: int,
        since: datetime,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> Iterator[DeckExtensionUpdateChunk]:
        # download_progress_cb gets passed the number of note customizations downloaded until now

        class Params(TypedDict, total=False):
            since: str
            size: int

        params: Params = {
            "since": since.strftime(ANKIHUB_DATETIME_FORMAT_STR) if since else None,
            "size": DECK_EXTENSION_UPDATE_PAGE_SIZE,
        }
        url = f"/deck_extensions/{deck_extension_id}/note_customizations/"

        i = 0
        customizations_count = 0
        while url is not None:
            response = self._send_request(
                "GET",
                url,
                params=params if i == 0 else None,
            )
            if response.status_code != 200:
                raise AnkiHubRequestError(response)

            data = response.json()
            url = data["next"].split("/api", maxsplit=1)[1] if data["next"] else None

            note_updates = DeckExtensionUpdateChunk.from_dict(data)
            yield note_updates

            i += 1
            customizations_count += len(note_updates.note_customizations)

            if download_progress_cb:
                download_progress_cb(customizations_count)

    def prevalidate_tag_groups(
        self, ankihub_deck_uuid: uuid.UUID, tag_group_names: List[str]
    ) -> List[TagGroupValidationResponse]:
        suggestions = [
            {"tag_group_name": tag_group_name} for tag_group_name in tag_group_names
        ]
        response = self._send_request(
            "POST",
            "/deck_extensions/suggestions/prevalidate",
            data={"deck_id": str(ankihub_deck_uuid), "suggestions": suggestions},
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        suggestions = data["suggestions"]
        tag_group_validation_objects = [
            TagGroupValidationResponse.from_dict(suggestion)
            for suggestion in suggestions
        ]
        return tag_group_validation_objects

    def suggest_optional_tags(
        self,
        suggestions: List[OptionalTagSuggestion],
        auto_accept: bool = False,
    ) -> None:
        deck_extension_ids = set(
            suggestion.deck_extension_id for suggestion in suggestions
        )
        for deck_extension_id in deck_extension_ids:
            suggestions_for_deck_extension = [
                suggestion
                for suggestion in suggestions
                if suggestion.deck_extension_id == deck_extension_id
            ]
            self._suggest_optional_tags_for_deck_extension(
                deck_extension_id=deck_extension_id,
                suggestions=suggestions_for_deck_extension,
                auto_accept=auto_accept,
            )

    def _suggest_optional_tags_for_deck_extension(
        self,
        deck_extension_id: int,
        suggestions: List[OptionalTagSuggestion],
        auto_accept: bool = False,
    ) -> None:
        response = self._send_request(
            "POST",
            f"/deck_extensions/{deck_extension_id}/suggestions/",
            data={
                "auto_accept": auto_accept,
                "suggestions": [suggestion.to_dict() for suggestion in suggestions],
            },
        )

        if response.status_code != 201:
            raise AnkiHubRequestError(response)

        data = response.json()
        message = data["message"]
        LOGGER.debug(f"suggest_optional_tags response message: {message}")

    def is_feature_flag_enabled(self, flag_name: str) -> bool:
        return (
            self._get_waffle_status()["flags"]
            .get(flag_name, {})
            .get("is_active", False)
        )

    def _get_waffle_status(self):
        response = self._send_request(
            "GET",
            "/waffle/waffle_status",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        return data


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
