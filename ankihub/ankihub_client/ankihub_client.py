import base64
import csv
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import urllib.parse
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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
    Set,
    TypedDict,
    Union,
    cast,
)
from zipfile import ZipFile

import requests
from requests import PreparedRequest, Request, Response, Session
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
)

from .models import (
    ANKIHUB_DATETIME_FORMAT_STR,
    CardReviewData,
    ChangeNoteSuggestion,
    Deck,
    DeckExtension,
    DeckExtensionUpdateChunk,
    DeckMediaUpdateChunk,
    DeckUpdateChunk,
    NewNoteSuggestion,
    NoteInfo,
    NoteSuggestion,
    OptionalTagSuggestion,
    TagGroupValidationResponse,
    UserDeckRelation,
    note_info_for_upload,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_APP_URL = "https://app.ankihub.net"
DEFAULT_API_URL = f"{DEFAULT_APP_URL}/api"
DEFAULT_S3_BUCKET_URL = "https://ankihub.s3.amazonaws.com"

STAGING_APP_URL = "https://staging.ankihub.net"
STAGING_API_URL = f"{STAGING_APP_URL}/api"
STAGING_S3_BUCKET_URL = "https://ankihub-staging.s3.amazonaws.com"

API_VERSION = 17.0

DECK_UPDATE_PAGE_SIZE = 2000  # seems to work well in terms of speed
DECK_EXTENSION_UPDATE_PAGE_SIZE = 2000
DECK_MEDIA_UPDATE_PAGE_SIZE = 2000

CSV_DELIMITER = ";"

CHUNK_BYTES_THRESHOLD = 67108864  # 60 megabytes

# Exceptions for which we should retry the request.
REQUEST_RETRY_EXCEPTION_TYPES = (
    requests.exceptions.JSONDecodeError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    ConnectionError,
    socket.gaierror,
    socket.timeout,
)

# Status codes for which we should retry the request.
RETRY_STATUS_CODES = {429}

IMAGE_FILE_EXTENSIONS = [
    ".png",
    ".jpeg",
    ".jpg",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
    ".webp",
]


def _should_retry_for_response(response: Response) -> bool:
    """Return True if the request should be retried for the given Response, False otherwise."""
    result = response.status_code in RETRY_STATUS_CODES or (
        500 <= response.status_code < 600
    )
    return result


RETRY_CONDITION = retry_if_result(_should_retry_for_response) | retry_if_exception_type(
    REQUEST_RETRY_EXCEPTION_TYPES
)


class AnkiHubHTTPError(Exception):
    """An unexpected HTTP code was returned in response to a request by the AnkiHub client."""

    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        return (
            f"AnkiHub request error: {self.response.status_code} {self.response.reason}"
        )


class AnkiHubRequestException(Exception):
    """An exception occurred while the AnkiHub client was making a request."""

    def __init__(self, original_exception):
        self.original_exception = original_exception
        self.__cause__ = original_exception

    def __str__(self):
        return f"AnkiHub request exception: {self.original_exception}"


class API(Enum):
    ANKIHUB = "ankihub"
    S3 = "s3"


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(
        self,
        local_media_dir_path_cb: Callable[[], Path],
        response_hooks=None,
        token: Optional[str] = None,
        get_token: Callable[[], str] = lambda: None,
        api_url: str = DEFAULT_API_URL,
        s3_bucket_url: str = DEFAULT_S3_BUCKET_URL,
    ):
        """Create a new AnkiHubClient.
        The token can be set with the token parameter or with the get_token parameter.
        The get_token parameter is a function that returns the token. It has priority over the token parameter.
        If both are set, the token parameter is ignored.
        """
        self.api_url = api_url
        self.s3_bucket_url = s3_bucket_url
        self.local_media_dir_path_cb = local_media_dir_path_cb
        self.token = token
        self.get_token = get_token
        self.response_hooks = response_hooks
        self.should_stop_background_threads = False

    def _send_request(
        self,
        method: str,
        api: API,
        url_suffix: str,
        json=None,
        data=None,
        files=None,
        params=None,
        stream=False,
    ) -> Response:
        """Send a request to an API. This method should be used for all requests.
        Logs the request and response.
        Retries the request if necessary.
        Uses appropriate headers for the given API.
        (The url_suffix is the part of the url after the base url.)
        """
        if api == API.ANKIHUB:
            url = f"{self.api_url}{url_suffix}"
        elif api == API.S3:
            url = f"{self.s3_bucket_url}{url_suffix}"
        else:
            raise ValueError(f"Unknown API: {api}")

        headers = {}
        if api == API.ANKIHUB:
            headers["Content-Type"] = "application/json"
            headers["Accept"] = f"application/json; version={API_VERSION}"

            # The value returned by self.get_token has priority over self.token
            token = self.token
            if self.get_token():
                token = self.get_token()

            if token:
                headers["Authorization"] = f"Token {token}"

        request = Request(
            method=method,
            url=url,
            json=json,
            data=data,
            files=files,
            params=params,
            headers=headers,
            hooks={"response": self.response_hooks} if self.response_hooks else None,
        )
        prepped = request.prepare()
        response = self._send_request_with_retry(prepped, stream=stream)

        return response

    def _send_request_with_retry(
        self, request: PreparedRequest, stream=False
    ) -> Response:
        """
        This method is only used in the _send_request method.
        Send a request, retrying if necessary.
        If the request fails after all retries, the last attempt's response is returned.
        If the last request failed because of an exception, that exception is raised.
        """
        try:
            response = self._send_request_with_retry_inner(request, stream=stream)
        except RetryError as e:
            # Catch RetryErrors to make the usage of tenacity transparent to the caller.
            last_attempt = cast(Future, e.last_attempt)
            # If the last attempt failed because of an exception, this will raise that exception.
            try:
                response = last_attempt.result()
            except Exception as e:
                raise AnkiHubRequestException(e) from e
        return response

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=RETRY_CONDITION,
    )
    def _send_request_with_retry_inner(
        self, request: PreparedRequest, stream=False
    ) -> Response:
        session = Session()
        try:
            response = session.send(request, stream=stream)
        finally:
            session.close()
        return response

    def login(self, credentials: dict) -> str:
        response = self._send_request("POST", API.ANKIHUB, "/login/", json=credentials)
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        token = response.json().get("token") if response else ""
        if token:
            self.token = token

        return token

    def signout(self) -> None:
        self.token = None
        response = self._send_request("POST", API.ANKIHUB, "/logout/")
        if response.status_code not in [204, 401]:
            raise AnkiHubHTTPError(response)

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

        s3_url_suffix = self._get_presigned_url_suffix(
            key=deck_file_name, action="upload"
        )

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

        self._upload_to_s3(s3_url_suffix, data)

        response = self._send_request(
            "POST",
            API.ANKIHUB,
            "/decks/",
            json={
                "key": deck_file_name,
                "name": deck_name,
                "anki_id": anki_deck_id,
                "is_private": private,
            },
        )
        if response.status_code != 201:
            raise AnkiHubHTTPError(response)

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

    def _gzip_decompress_string(self, string: bytes) -> str:
        result = gzip.decompress(string).decode("utf-8")
        return result

    def _upload_to_s3(
        self, s3_url_suffix: str, data: Union[bytes, BufferedReader]
    ) -> None:
        s3_response = self._send_request(
            "PUT",
            API.S3,
            s3_url_suffix,
            data=data,
        )
        if s3_response.status_code != 200:
            raise AnkiHubHTTPError(s3_response)

    def generate_media_files_with_hashed_names(
        self, media_file_paths: Sequence[Path]
    ) -> Dict[str, str]:
        """Generates a filename for each file in the list of paths by hashing the file.
        The file is copied to the new name. If the file already exists, it is skipped,
        but the mapping still will be made with the existing filename.
        Returns a map of the old filename to the new filename.
        """
        result: Dict[str, str] = {}
        for for_old_media_path in media_file_paths:
            # First we check if the media file exists locally.
            # If no, we skip this iteration.
            if not for_old_media_path.is_file():
                continue

            # Generate a hash from the file's content
            with for_old_media_path.open("rb") as media_file:
                file_content_hash = hashlib.md5(media_file.read())

            # Store the new filename under the old filename key in the dict
            # that will be returned
            new_media_path = for_old_media_path.parent / (
                file_content_hash.hexdigest() + for_old_media_path.suffix
            )

            if self._media_file_should_be_converted_to_webp(for_old_media_path):
                # The lambda will convert images to the webp format if they are uploaded with a .webp extension and
                # are not already webp images.
                new_media_path = new_media_path.with_suffix(".webp")

            # If the file with the hashed name does not exist already, we
            # try to create it.
            if not new_media_path.is_file():
                try:
                    # Copy the file with the new name at the same location of the
                    # original file
                    shutil.copyfile(for_old_media_path, new_media_path)
                except shutil.SameFileError:
                    continue

            result[for_old_media_path.name] = new_media_path.name

        return result

    def _media_file_should_be_converted_to_webp(self, media_path: Path) -> bool:
        """Whether the media file should be converted to webp once its uploaded to s3."""
        # We don't want to convert svgs, because they don't benefit from the conversion in most cases.
        result = (
            media_path.suffix.lower() in IMAGE_FILE_EXTENSIONS
            and media_path.suffix.lower() not in [".svg", ".webp"]
        )
        return result

    def upload_media(self, media_paths: Set[Path], ah_did: uuid.UUID) -> None:
        # Create chunks of media paths to zip and upload each chunk individually.
        # Each chunk is divided based on the size of all media files in that chunk to
        # create chunks of similar size.
        media_path_chunks: List[List[Path]] = []
        chunk: List[Path] = []
        current_chunk_size_bytes = 0
        for media_path in media_paths:
            if media_path.is_file():
                current_chunk_size_bytes += media_path.stat().st_size
                chunk.append(media_path)

            if current_chunk_size_bytes > CHUNK_BYTES_THRESHOLD:
                media_path_chunks.append(chunk)
                current_chunk_size_bytes = 0
                chunk = []
            else:
                # We need this so we don't lose chunks of smaller size
                # that didn't reach the threshold (usually the "tail"
                # of the list, but it can also happen if we have just
                # a few media files and all of them sum up to less than the threshold
                # right on the first chunk)
                if media_path == list(media_paths)[-1]:
                    # Check if we're leaving the loop (last iteration) - if yes,
                    # just close this small chunk before leaving.
                    media_path_chunks.append(chunk)

        # Get a S3 presigned URL that allows uploading multiple files with a given prefix
        s3_presigned_info = self._get_presigned_url_for_multiple_uploads(
            prefix=f"deck_assets/{ah_did}"
        )

        # Use ThreadPoolExecutor to zip & upload media files
        with ThreadPoolExecutor() as executor:
            futures: List[Future] = []
            for chunk_number, chunk in enumerate(media_path_chunks):
                futures.append(
                    executor.submit(
                        self._zip_and_upload_media_chunk,
                        chunk,
                        chunk_number,
                        ah_did,
                        s3_presigned_info,
                    )
                )

            for future in as_completed(futures):
                future.result()

                if self.should_stop_background_threads:
                    for future in futures:
                        future.cancel()
                    return

    def _zip_and_upload_media_chunk(
        self,
        chunk: List[Path],
        chunk_number: int,
        ah_did: uuid.UUID,
        s3_presigned_info: dict,
    ) -> None:
        # Zip the media files found locally
        zip_filepath = Path(
            self.local_media_dir_path_cb()
            / f"{ah_did}_{chunk_number}_deck_assets_part.zip"
        )
        LOGGER.info(f"Creating zipped media file [{zip_filepath.name}]")
        with ZipFile(zip_filepath, "w") as media_zip:
            for media_path in chunk:
                if media_path.is_file():
                    media_zip.write(media_path, arcname=media_path.name)

        # Upload to S3
        LOGGER.info(f"Uploading file [{zip_filepath.name}] to S3")
        self._upload_file_to_s3_with_reusable_presigned_url(
            s3_presigned_info=s3_presigned_info, filepath=zip_filepath
        )
        LOGGER.info(f"Successfully uploaded [{zip_filepath.name}]")

        # Remove the zip file from the local machine after the upload
        LOGGER.info(f"Removing file [{zip_filepath.name}] from local files")
        try:
            os.remove(zip_filepath)
        except FileNotFoundError:
            LOGGER.warning(
                f"Could not remove file [{zip_filepath.name}] from local files."
            )

    def _upload_file_to_s3_with_reusable_presigned_url(
        self, s3_presigned_info: dict, filepath: Path
    ) -> None:
        """Opens and uploads the file data to S3 using a reusable presigned URL. Useful when uploading
        multiple media files to the same path while keeping the original filename.
        :param s3_presigned_info: dict with the reusable presigned URL info.
                                  Obtained as the return of 'get_presigned_url_for_multiple_uploads'
        :param filepath: the Path object with the location of the file in the system
        -"""
        with open(filepath, "rb") as data:
            url: str = s3_presigned_info["url"]
            url_suffix = url.split(self.s3_bucket_url)[1]
            s3_response = self._send_request(
                "POST",
                API.S3,
                url_suffix=url_suffix,
                data=s3_presigned_info["fields"],
                files={"file": (filepath.name, data)},
            )

        if s3_response.status_code != 204:
            raise AnkiHubHTTPError(s3_response)

    def download_media(self, media_names: List[str], deck_id: uuid.UUID) -> None:
        deck_media_remote_dir = f"/deck_assets/{deck_id}/"
        with ThreadPoolExecutor() as executor:
            media_dir_path = self.local_media_dir_path_cb()
            futures: List[Future] = []
            for media_name in media_names:
                media_path = media_dir_path / media_name
                media_remote_path = deck_media_remote_dir + urllib.parse.quote_plus(
                    media_name
                )

                # First we check if the media file already exists.
                # If yes, we skip this iteration.
                if os.path.isfile(media_path):
                    continue

                futures.append(
                    executor.submit(self._download_media, media_path, media_remote_path)
                )

            for future in as_completed(futures):
                if self.should_stop_background_threads:
                    for future in futures:
                        future.cancel()
                    return

                future.result()

            LOGGER.info("Downloaded media from AnkiHub.")

    def _download_media(self, media_file_path: Path, media_remote_path: str):
        response = self._send_request("GET", API.S3, media_remote_path, stream=True)
        # Log and skip this iteration if the response is not 200 OK
        if response.ok:
            # If we get a valid response, open the file and write the content
            with open(media_file_path, "wb") as file:
                for block in response.iter_content(1024):
                    if not block:
                        break

                    file.write(block)

                    if self.should_stop_background_threads:
                        # Remove incomplete file if the download was interrupted
                        file.close()
                        media_file_path.unlink()
                        return
        else:
            LOGGER.info(
                f"Unable to download media file [{media_remote_path}]. Response status code: {response.status_code}"
            )

    def stop_background_threads(self) -> None:
        """Can be called to stop all background threads started by this client."""
        self.should_stop_background_threads = True

    def allow_background_threads(self) -> None:
        """Can be called to allow background threads to run after they were stopped previously."""
        self.should_stop_background_threads = False

    def get_deck_subscriptions(self) -> List[Deck]:
        response = self._send_request("GET", API.ANKIHUB, "/decks/subscriptions/")
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        return [Deck.from_dict(deck["deck"]) for deck in response.json()]

    def get_decks_with_user_relation(self) -> List[Deck]:
        response = self._send_request("GET", API.ANKIHUB, "/users/decks/")
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        return [Deck.from_dict(deck) for deck in response.json()]

    def get_owned_decks(self) -> List[Deck]:
        decks = self.get_decks_with_user_relation()
        result = [
            deck for deck in decks if deck.user_relation == UserDeckRelation.OWNER
        ]
        return result

    def subscribe_to_deck(self, deck_id: uuid.UUID) -> None:
        response = self._send_request(
            "POST", API.ANKIHUB, "/decks/subscriptions/", json={"deck": str(deck_id)}
        )
        if response.status_code != 201:
            raise AnkiHubHTTPError(response)

    def unsubscribe_from_deck(self, deck_id: uuid.UUID) -> None:
        response = self._send_request(
            "DELETE", API.ANKIHUB, f"/decks/{deck_id}/subscriptions/"
        )
        if response.status_code not in (204, 404):
            raise AnkiHubHTTPError(response)

    def download_deck(
        self,
        ah_did: uuid.UUID,
        download_progress_cb: Optional[Callable[[int], None]] = None,
    ) -> List[NoteInfo]:
        deck_info = self.get_deck_by_id(ah_did)

        s3_url_suffix = self._get_presigned_url_suffix(
            key=deck_info.csv_notes_filename, action="download"
        )

        if download_progress_cb:
            s3_response_content = self._download_with_progress_cb(
                s3_url_suffix, download_progress_cb
            )
        else:
            s3_response = self._send_request("GET", API.S3, s3_url_suffix)
            if s3_response.status_code != 200:
                raise AnkiHubHTTPError(s3_response)
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
        self, s3_url_suffix: str, progress_cb: Callable[[int], None]
    ) -> bytes:
        with self._send_request("GET", API.S3, s3_url_suffix, stream=True) as response:
            if response.status_code != 200:
                raise AnkiHubHTTPError(response)

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
        ah_did: uuid.UUID,
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
        url_suffix = f"/decks/{ah_did}/updates"
        notes_count = 0
        first_request = True
        while url_suffix is not None:
            response = self._send_request(
                "GET",
                API.ANKIHUB,
                url_suffix,
                params=params if first_request else None,
            )
            if response.status_code != 200:
                raise AnkiHubHTTPError(response)

            data = response.json()
            url_suffix = (
                data["next"].split("/api", maxsplit=1)[1] if data["next"] else None
            )

            # decompress and transform notes data
            notes_data_base85 = data["notes"]
            notes_data_gzipped = base64.b85decode(notes_data_base85)
            notes_data = json.loads(self._gzip_decompress_string(notes_data_gzipped))
            data["notes"] = _transform_notes_data(notes_data)

            note_updates = DeckUpdateChunk.from_dict(data)
            yield note_updates

            notes_count += len(note_updates.notes)

            if download_progress_cb:
                download_progress_cb(notes_count)

            first_request = False

    def get_deck_media_updates(
        self,
        ah_did: uuid.UUID,
        since: datetime,
    ) -> Iterator[DeckMediaUpdateChunk]:
        class Params(TypedDict, total=False):
            since: str
            size: int

        params: Params = {
            "since": since.strftime(ANKIHUB_DATETIME_FORMAT_STR) if since else None,
            "size": DECK_MEDIA_UPDATE_PAGE_SIZE,
        }
        url_suffix = f"/decks/{ah_did}/media/list/"
        first_request = True
        while url_suffix is not None:
            response = self._send_request(
                "GET",
                API.ANKIHUB,
                url_suffix,
                params=params if first_request else None,
            )
            if response.status_code != 200:
                raise AnkiHubHTTPError(response)

            data = response.json()
            url_suffix = (
                data["next"].split("/api", maxsplit=1)[1] if data["next"] else None
            )

            media_updates = DeckMediaUpdateChunk.from_dict(data)
            yield media_updates

            first_request = False

    def get_deck_by_id(self, ah_did: uuid.UUID) -> Deck:
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            f"/decks/{ah_did}/",
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        result = Deck.from_dict(data)
        return result

    def get_note_by_id(self, ah_nid: uuid.UUID) -> NoteInfo:
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            f"/notes/{ah_nid}",
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

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
            API.ANKIHUB,
            f"/notes/{change_note_suggestion.ah_nid}/suggestion/",
            json={**change_note_suggestion.to_dict(), "auto_accept": auto_accept},
        )
        if response.status_code != 201:
            raise AnkiHubHTTPError(response)

    def create_new_note_suggestion(
        self,
        new_note_suggestion: NewNoteSuggestion,
        auto_accept: bool = False,
    ):
        response = self._send_request(
            "POST",
            API.ANKIHUB,
            f"/decks/{new_note_suggestion.ah_did}/note-suggestion/",
            json={**new_note_suggestion.to_dict(), "auto_accept": auto_accept},
        )
        if response.status_code != 201:
            raise AnkiHubHTTPError(response)

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
            API.ANKIHUB,
            url_suffix=url,
            json={
                "suggestions": [d.to_dict() for d in suggestions],
                "auto_accept": auto_accept,
            },
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        errors_by_anki_nid = {
            suggestion.anki_nid: d["validation_errors"]
            for d, suggestion in zip(data, suggestions)
            if d.get("validation_errors")
        }
        return errors_by_anki_nid

    def _get_presigned_url_suffix(self, key: str, action: str) -> str:
        """
        Get presigned URL suffix for S3 to upload a single file.
        The suffix is the part of the URL after the base url.
        :param key: s3 key
        :param action: upload or download
        :return: the presigned url suffix
        """
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            "/decks/generate-presigned-url",
            params={"key": key, "type": action, "many": "false"},
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        url = response.json()["pre_signed_url"]
        result = url.split(self.s3_bucket_url)[1]
        return result

    def _get_presigned_url_for_multiple_uploads(self, prefix: str) -> dict:
        """
        Get presigned URL for S3 to upload multiple files. Useful when uploading
        multiple media files to the same path while keeping the original filename.
        :param prefix: the path in S3 where the files will be uploaded
        :return: a dict with the required data to build the upload request
        """
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            "/decks/generate-presigned-url",
            params={"key": prefix, "type": "upload", "many": "true"},
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        result = response.json()["pre_signed_url"]
        return result

    def get_note_type(self, anki_note_type_id: int) -> Dict[str, Any]:
        response = self._send_request(
            "GET", API.ANKIHUB, f"/note-types/{anki_note_type_id}/"
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        result = _to_anki_note_type(data)
        return result

    def get_protected_fields(self, ah_did: uuid.UUID) -> Dict[int, List[str]]:
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            f"/decks/{ah_did}/protected-fields/",
        )
        if response.status_code == 404:
            return {}
        elif response.status_code != 200:
            raise AnkiHubHTTPError(response)

        protected_fields_raw = response.json()["fields"]
        result = {
            int(field_id): field_names
            for field_id, field_names in protected_fields_raw.items()
        }
        return result

    def get_protected_tags(self, ah_did: uuid.UUID) -> List[str]:
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            f"/decks/{ah_did}/protected-tags/",
        )
        if response.status_code == 404:
            return []
        elif response.status_code != 200:
            raise AnkiHubHTTPError(response)

        result = response.json()["tags"]
        result = [x for x in result if x.strip()]
        return result

    def get_media_disabled_fields(self, ah_did: uuid.UUID) -> Dict[int, List[str]]:
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            f"/decks/{ah_did}/media-disabled-fields/",
        )
        if response.status_code == 404:
            return {}
        elif response.status_code != 200:
            raise AnkiHubHTTPError(response)

        protected_fields_raw = response.json()["fields"]
        result = {
            int(field_id): field_names
            for field_id, field_names in protected_fields_raw.items()
        }
        return result

    def get_deck_extensions(self) -> List[DeckExtension]:
        response = self._send_request("GET", API.ANKIHUB, "/users/deck_extensions")
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        extension_dicts = data.get("deck_extensions", [])
        result = [DeckExtension.from_dict(d) for d in extension_dicts]
        return result

    def get_deck_extensions_by_deck_id(self, deck_id: uuid.UUID) -> List[DeckExtension]:
        response = self._send_request(
            "GET", API.ANKIHUB, "/users/deck_extensions", params={"deck_id": deck_id}
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

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
                API.ANKIHUB,
                url,
                params=params if i == 0 else None,
            )
            if response.status_code != 200:
                raise AnkiHubHTTPError(response)

            data = response.json()
            url = data["next"].split("/api", maxsplit=1)[1] if data["next"] else None

            note_updates = DeckExtensionUpdateChunk.from_dict(data)
            yield note_updates

            i += 1
            customizations_count += len(note_updates.note_customizations)

            if download_progress_cb:
                download_progress_cb(customizations_count)

    def prevalidate_tag_groups(
        self, ah_did: uuid.UUID, tag_group_names: List[str]
    ) -> List[TagGroupValidationResponse]:
        suggestions = [
            {"tag_group_name": tag_group_name} for tag_group_name in tag_group_names
        ]
        response = self._send_request(
            "POST",
            API.ANKIHUB,
            "/deck_extensions/suggestions/prevalidate",
            json={"deck_id": str(ah_did), "suggestions": suggestions},
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

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
            API.ANKIHUB,
            f"/deck_extensions/{deck_extension_id}/suggestions/",
            json={
                "auto_accept": auto_accept,
                "suggestions": [suggestion.to_dict() for suggestion in suggestions],
            },
        )

        if response.status_code != 201:
            raise AnkiHubHTTPError(response)

        data = response.json()
        message = data["message"]
        LOGGER.debug(f"suggest_optional_tags response message: {message}")

    def get_feature_flags(self) -> Dict[str, bool]:
        """Returns a dict of feature flags to their status (enabled or disabled)."""
        response = self._send_request(
            "GET",
            API.ANKIHUB,
            "/feature-flags",
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        result = {
            flag_name: flag_data["is_active"]
            for flag_name, flag_data in data["flags"].items()
        }
        return result

    def is_media_upload_finished(self, ah_did: uuid.UUID) -> bool:
        deck_info = self.get_deck_by_id(ah_did)
        return deck_info.media_upload_finished

    def media_upload_finished(self, ah_did: uuid.UUID) -> None:
        response = self._send_request(
            "PATCH",
            API.ANKIHUB,
            f"/decks/{ah_did}/media-upload-finished",
        )
        if response.status_code != 204:
            raise AnkiHubHTTPError(response)

    def owned_deck_ids(self) -> List[uuid.UUID]:
        response = self._send_request("GET", API.ANKIHUB, "/users/me")
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)

        data = response.json()
        result = [uuid.UUID(deck["id"]) for deck in data["created_decks"]]
        return result

    def send_card_review_data(self, card_review_data: List[CardReviewData]) -> None:
        response = self._send_request(
            "POST",
            API.ANKIHUB,
            "/users/card-review-data/",
            json=[review.to_dict() for review in card_review_data],
        )
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)


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
    note_type_data["id"] = note_type_data.pop("anki_id")
    note_type_data["tmpls"] = note_type_data.pop("templates")
    note_type_data["flds"] = note_type_data.pop("fields")
    return note_type_data
