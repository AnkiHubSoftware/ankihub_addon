import json
from json import JSONDecodeError
from pathlib import Path
from pprint import pformat

import requests
from requests import Response

from . import LOGGER
from .ankihub_client import AnkiHubClient, AnkiHubRequestError
from .config import config


def logging_hook(response: Response, *args, **kwargs):
    endpoint = response.request.url
    method = response.request.method
    body = response.request.body
    body = json.loads(body) if body else body
    if "/login/" in endpoint:
        body.pop("password")  # type: ignore
    headers = response.request.headers
    LOGGER.debug(
        f"request: {method} {endpoint}\ndata={pformat(body)}\nheaders={headers}"
    )
    LOGGER.debug(f"response status: {response.status_code}")
    try:
        LOGGER.debug(f"response content: {pformat(response.json())}")
    except JSONDecodeError:
        LOGGER.debug(f"response content: {str(response.content)}")
    else:
        LOGGER.debug(f"response: {response}")
    return response


DEFAULT_RESPONSE_HOOKS = [
    logging_hook,
]


class AddonAnkiHubClient(AnkiHubClient):
    def __init__(self, hooks=None) -> None:
        super().__init__(
            hooks=hooks if hooks is not None else DEFAULT_RESPONSE_HOOKS,
            token=config.private_config.token,
        )

    def upload_logs(self, file: Path, key: str) -> Response:
        presigned_url_response = self.get_presigned_url(key=key, action="upload")
        if presigned_url_response.status_code != 200:
            return presigned_url_response

        s3_url = presigned_url_response.json()["pre_signed_url"]
        with open(file, "rb") as f:
            log_data = f.read()

        s3_response = requests.put(s3_url, data=log_data)
        if s3_response.status_code != 200:
            raise AnkiHubRequestError(s3_response)

        return s3_response
