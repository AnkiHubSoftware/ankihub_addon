import csv
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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
from zipfile import ZipFile

import requests
from requests import PreparedRequest, Request, Response, Session

from .models import (
    ANKIHUB_DATETIME_FORMAT_STR,
    ChangeNoteSuggestion,
    Deck,
    DeckExtension,
    DeckExtensionUpdateChunk,
    DeckUpdateChunk,
    NewNoteSuggestion,
    NoteInfo,
    NoteSuggestion,
    OptionalTagSuggestion,
    TagGroupValidationResponse,
    note_info_for_upload,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_APP_URL = "https://app.ankihub.net"
DEFAULT_API_URL = f"{DEFAULT_APP_URL}/api"
DEFAULT_S3_BUCKET_URL = "https://ankihub.s3.amazonaws.com"

STAGING_APP_URL = "https://staging.ankihub.net"
STAGING_API_URL = f"{STAGING_APP_URL}/api"
STAGING_S3_BUCKET_URL = "https://ankihub-staging.s3.amazonaws.com"

API_VERSION = 9.0

DECK_UPDATE_PAGE_SIZE = 2000  # seems to work well in terms of speed
DECK_EXTENSION_UPDATE_PAGE_SIZE = 2000

CSV_DELIMITER = ";"

CHUNK_BYTES_THRESHOLD = 67108864  # 60 megabytes


class AnkiHubRequestError(Exception):
    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        return (
            f"AnkiHub request error: {self.response.status_code} {self.response.reason}"
        )


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(
        self,
        hooks=None,
        token: Optional[str] = None,
        api_url: str = DEFAULT_API_URL,
        s3_bucket_url: str = DEFAULT_S3_BUCKET_URL,
        local_media_dir_path: Optional[Path] = None,
    ):
        # If local_media_dir_path is None, then calling some methods related to media will fail.
        self.api_url = api_url
        self.s3_bucket_url = s3_bucket_url
        self.local_media_dir_path = local_media_dir_path

        self.session = Session()

        if hooks is not None:
            self.session.hooks["response"] = hooks

        self.session.headers.update({"Content-Type": "application/json"})
        self.session.headers.update(
            {"Accept": f"application/json; version={API_VERSION}"}
        )
        if token:
            self.session.headers["Authorization"] = f"Token {token}"

    def _build_request(
        self,
        method,
        endpoint,
        data=None,
        params=None,
    ) -> PreparedRequest:
        url = f"{self.api_url}{endpoint}"
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

        s3_url = self._get_presigned_url(key=deck_file_name, action="upload")

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

    def _gzip_compress_string(self, string: str) -> bytes:
        result = gzip.compress(
            bytes(
                string,
                encoding="utf-8",
            )
        )
        return result

    def _upload_to_s3(self, s3_url: str, data: Union[bytes, BufferedReader]) -> None:
        s3_response = requests.put(
            s3_url,
            data=data,
        )
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

    def generate_asset_files_with_hashed_names(
        self, paths: Sequence[Path]
    ) -> Dict[str, str]:
        """Generates a filename for each file in the list of paths by hashing the file.
        The file is copied to the new name. If the file already exists, it is skipped,
        but the mapping still will be made with the existing filename.
        Returns a map of the old filename to the new filename.
        """
        result: Dict[str, str] = {}
        for old_asset_path in paths:
            # First we check if the image exists locally.
            # If no, we skip this iteration.
            if not old_asset_path.is_file():
                continue

            # Generate a hash from the file's content
            with old_asset_path.open("rb") as asset:
                file_content_hash = hashlib.md5(asset.read())

            # Store the new filename under the old filename key in the dict
            # that will be returned
            new_asset_path = old_asset_path.parent / (
                file_content_hash.hexdigest() + old_asset_path.suffix
            )

            # If the file with the hashed name does not exist already, we
            # try to create it.
            if not new_asset_path.is_file():
                try:
                    # Copy the file with the new name at the same location of the
                    # original file
                    shutil.copyfile(old_asset_path, new_asset_path)
                except shutil.SameFileError:
                    continue

            result[old_asset_path.name] = new_asset_path.name

        return result

    def upload_assets(self, image_paths: List[Path], ah_did: uuid.UUID) -> None:
        # Create chunks of image paths to zip and upload each chunk individually.
        # Each chunk is divided based on the size of all images on that chunk to
        # create chunks of similar size.
        image_path_chunks: List[List[Path]] = []
        chunk: List[Path] = []
        current_chunk_size_bytes = 0
        for image_path in image_paths:
            if image_path.is_file():
                current_chunk_size_bytes += image_path.stat().st_size
                chunk.append(image_path)

            if current_chunk_size_bytes > CHUNK_BYTES_THRESHOLD:
                image_path_chunks.append(chunk)
                current_chunk_size_bytes = 0
                chunk = []
            else:
                # We need this so we don't lose chunks of smaller size
                # that didn't reach the threshold (usually the "tail"
                # of the image list, but can also happen if we have just
                # a few images and all of them sum up to less than the threshold
                # right on the first chunk)
                if image_path == list(image_paths)[-1]:
                    # Check if we're leaving the loop (last iteration) - if yes,
                    # just close this small chunk before leaving.
                    image_path_chunks.append(chunk)

        # Get a S3 presigned URL that allows uploading multiple files with a given prefix
        s3_presigned_info = self._get_presigned_url_for_multiple_uploads(
            prefix=f"deck_assets/{ah_did}"
        )

        # Use ThreadPoolExecutor to zip & upload assets
        futures = []
        with ThreadPoolExecutor() as executor:
            for chunk_number, chunk in enumerate(image_path_chunks):
                futures.append(
                    executor.submit(
                        self._zip_and_upload_assets_chunk,
                        chunk,
                        chunk_number,
                        ah_did,
                        s3_presigned_info,
                    )
                )

            for future in as_completed(futures):
                future.result()

    def _zip_and_upload_assets_chunk(
        self,
        chunk: List[Path],
        chunk_number: int,
        ah_did: uuid.UUID,
        s3_presigned_info: dict,
    ):
        # TODO: Error logging/handling

        # Zip the images found locally
        zip_filepath = Path(
            self.local_media_dir_path / f"{ah_did}_{chunk_number}_deck_assets_part.zip"
        )
        LOGGER.info(f"Creating zipped asset file [{zip_filepath.name}]")
        with ZipFile(zip_filepath, "w") as img_zip:
            for img_path in chunk:
                if img_path.is_file():
                    img_zip.write(img_path, arcname=img_path.name)

        # Upload to S3
        LOGGER.info(f"Uploading file [{zip_filepath.name}] to S3")
        self._upload_file_to_s3_with_reusable_presigned_url(
            s3_presigned_info=s3_presigned_info, filepath=zip_filepath
        )

        # Remove the zip file from the local machine after the upload
        LOGGER.info(f"Removing file [{zip_filepath.name}] from local files")
        os.remove(zip_filepath)

        LOGGER.info(f"Successfully uploaded [{zip_filepath.name}]")

    def _upload_file_to_s3_with_reusable_presigned_url(
        self, s3_presigned_info: dict, filepath: Path
    ) -> None:
        """Opens and uploads the file data to S3 using a reusable presigned URL. Useful when uploading
        multiple assets to the same path while keeping the original filename.
        :param s3_presigned_info: dict with the reusable presigned URL info.
                                  Obtained as the return of 'get_presigned_url_for_multiple_uploads'
        :param filepath: the Path object with the location of the file in the system
        -"""
        with open(filepath, "rb") as data:
            s3_response = requests.post(
                s3_presigned_info["url"],
                data=s3_presigned_info["fields"],
                files={"file": (filepath.name, data)},
            )

        if s3_response.status_code != 204:
            raise AnkiHubRequestError(s3_response)

    def download_images(self, img_names: List[str], deck_id: uuid.UUID) -> None:
        deck_images_remote_dir = f"{self.s3_bucket_url}/deck_assets/{deck_id}/"
        futures = []
        with ThreadPoolExecutor() as executor:
            for img_name in img_names:
                img_path = self.local_media_dir_path / img_name

                img_remote_path = deck_images_remote_dir + urllib.parse.quote_plus(
                    img_name
                )

                # First we check if the image already exists.
                # If yes, we skip this iteration.
                if os.path.isfile(img_path):
                    continue

                futures.append(
                    executor.submit(self._download_image, img_path, img_remote_path)
                )

            for future in as_completed(futures):
                future.result()
            LOGGER.info("Downloaded images from AnkiHub.")

    def _download_image(self, img_path, img_remote_path):
        response = requests.get(img_remote_path, stream=True)
        # Log and skip this iteration if the response is not 200 OK
        if response.ok:
            # If we get a valid response, open the file and write the content
            with open(img_path, "wb") as handle:
                for block in response.iter_content(1024):
                    if not block:
                        break

                    handle.write(block)
        else:
            LOGGER.info(
                f"Unable to download image [{img_remote_path}]. Response status code: {response.status_code}"
            )

    def download_deck(
        self,
        ankihub_deck_uuid: uuid.UUID,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> List[NoteInfo]:
        deck_info = self.get_deck_by_id(ankihub_deck_uuid)

        s3_url = self._get_presigned_url(
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
        notes_data_raw = _transform_notes_data(notes_data_raw)
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

            data["notes"] = _transform_notes_data(data["notes"])
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
        new_note_suggestions: Sequence[NewNoteSuggestion] = [],
        change_note_suggestions: Sequence[ChangeNoteSuggestion] = [],
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

    def _get_presigned_url(self, key: str, action: str) -> str:
        """
        Get presigned URL for S3 to upload a single file
        :param key: deck name
        :param action: upload or download
        :return: the pre signed url as a string
        """
        method = "GET"
        endpoint = "/decks/generate-presigned-url"
        data = {"key": key, "type": action, "many": "false"}
        response = self._send_request(method, endpoint, params=data)
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        result = response.json()["pre_signed_url"]
        return result

    def _get_presigned_url_for_multiple_uploads(self, prefix: str) -> dict:
        """
        Get presigned URL for S3 to upload multiple files. Useful when uploading
        multiple assets to the same path while keeping the original filename.
        :param prefix: the path in S3 where the files will be uploaded
        :return: a dict with the required data to build the upload request
        """
        method = "GET"
        endpoint = "/decks/generate-presigned-url"
        data = {"key": prefix, "type": "upload", "many": "true"}
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
        result = _to_anki_note_type(data)
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

    def get_asset_disabled_fields(
        self, ankihub_deck_uuid: uuid.UUID
    ) -> Dict[int, List[str]]:
        response = self._send_request(
            "GET",
            f"/decks/{ankihub_deck_uuid}/asset-disabled-fields/",
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
            self._get_feature_flags_status()["flags"]
            .get(flag_name, {})
            .get("is_active", False)
        )

    def _get_feature_flags_status(self):
        response = self._send_request(
            "GET",
            "/feature-flags",
        )
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        return data

    def is_image_upload_finished(self, ankihub_deck_uuid: uuid.UUID) -> bool:
        deck_info = self.get_deck_by_id(ankihub_deck_uuid)
        return deck_info.image_upload_finished

    def image_upload_finished(self, ankihub_deck_uuid: uuid.UUID) -> None:
        response = self._send_request(
            "PATCH",
            f"/decks/{ankihub_deck_uuid}/image-upload-finished",
        )
        if response.status_code != 204:
            raise AnkiHubRequestError(response)

    def owned_deck_ids(self) -> List[uuid.UUID]:
        response = self._send_request("GET", "/users/me")
        if response.status_code != 200:
            raise AnkiHubRequestError(response)

        data = response.json()
        result = [uuid.UUID(deck["id"]) for deck in data["created_decks"]]
        return result


def _transform_notes_data(notes_data: List[Dict]) -> List[Dict]:
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


def _to_anki_note_type(note_type_data: Dict) -> Dict[str, Any]:
    """Turn JSON response from AnkiHubClient.get_note_type into NotetypeDict."""
    del note_type_data["anki_id"]
    note_type_data["tmpls"] = note_type_data.pop("templates")
    note_type_data["flds"] = note_type_data.pop("fields")
    return note_type_data