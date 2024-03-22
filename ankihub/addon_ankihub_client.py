"""AddonAnkiHubClient is a wrapper around AnkiHubClient that is adapted to the AnkiHub add-on.
It should be used instead of AnkiHubClient in the AnkiHub add-on."""
import json
from copy import deepcopy
from json import JSONDecodeError
from pathlib import Path
from pprint import pformat
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

    body_dict: Optional[Dict] = None
    try:
        body_dict = json.loads(body) if body else None
    except ValueError:
        pass

    if "/login/" in endpoint:
        body_dict.pop("password")

    headers_copy = deepcopy(response.request.headers)
    if headers_copy.get("Authorization"):
        headers_copy["Authorization"] = "<redacted>"

    LOGGER.info(
        f"request: {method} {endpoint}\nheaders={headers_copy}"
        + (f"\ndata={pformat(body_dict)}" if body_dict else "")
    )
    LOGGER.info(f"response: {response}")
    if response.status_code < 400:
        try:
            LOGGER.debug(f"response content: {pformat(response.json())}")
        except JSONDecodeError:
            # We don't want to log the content of the response if it's not JSON
            pass
    else:
        LOGGER.info(f"response content: {response.text}")

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
            local_media_dir_path_cb=lambda: Path(aqt.mw.col.media.dir())
            if aqt.mw.col
            else None,
        )

    def upload_logs(self, file: Path, key: str) -> None:
        with open(file, "rb") as f:
            log_data = f.read()

        s3_url_suffix = self._presigned_url_suffix_from_key(key=key, action="upload")
        s3_response = self._send_request("PUT", API.S3, s3_url_suffix, data=log_data)
        if s3_response.status_code != 200:
            raise AnkiHubHTTPError(s3_response)
