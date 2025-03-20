"""AddonAnkiHubClient is a wrapper around AnkiHubClient that is adapted to the AnkiHub add-on.
It should be used instead of AnkiHubClient in the AnkiHub add-on."""

import json
from copy import deepcopy
from json import JSONDecodeError
from pathlib import Path
from typing import Dict, Optional

import aqt
from requests import Response

from . import LOGGER
from .ankihub_client import AnkiHubClient, AnkiHubHTTPError
from .ankihub_client.ankihub_client import API
from .settings import config


def logging_hook(response: Response, *args, **kwargs) -> Response:
    endpoint = response.request.url
    method = response.request.method
    body = response.request.body

    if method == "GET" and "s3" in endpoint and "deck_assets" in endpoint:
        # Don't log the request for downloading deck assets, as this would result in a lot of noise,
        # because each asset is downloaded separately.
        return response

    body_dict: Optional[Dict] = None
    try:
        body_dict = json.loads(body) if body else None
    except ValueError:
        pass

    if "/login/" in endpoint:
        body_dict.pop("password")

    sanitized_headers = deepcopy(response.request.headers)
    if sanitized_headers.get("Authorization"):
        sanitized_headers["Authorization"] = "<redacted>"

    LOGGER.info(
        "AnkiHubClient HTTP Transaction",
        method=method,
        endpoint=endpoint,
        headers=sanitized_headers,
        request_body=body_dict,
        response_status=response.status_code,
    )
    if response.status_code < 400:
        try:
            LOGGER.debug("Response content", content=json.loads(response.text))
        except JSONDecodeError:
            # We don't want to log the content of the response if it's not JSON
            pass
    else:
        LOGGER.info("Response content", content=response.text)

    return response


DEFAULT_RESPONSE_HOOKS = [
    logging_hook,
]


class AddonAnkiHubClient(AnkiHubClient):
    def __init__(self, hooks=None) -> None:
        super().__init__(
            api_url=config.api_url,
            s3_bucket_url=config.s3_bucket_url,
            response_hooks=hooks if hooks is not None else DEFAULT_RESPONSE_HOOKS,
            get_token=lambda: config.token(),
            local_media_dir_path_cb=lambda: (
                Path(aqt.mw.col.media.dir()) if aqt.mw.col else None
            ),
        )

    def upload_logs(self, file: Path, key: str) -> None:
        with open(file, "rb") as f:
            log_data = f.read()

        s3_url_suffix = self._presigned_url_suffix_from_key(key=key, action="upload")
        s3_response = self._send_request(
            "PUT", API.S3, s3_url_suffix, data=log_data, is_long_running=True
        )
        if s3_response.status_code != 200:
            raise AnkiHubHTTPError(s3_response)
