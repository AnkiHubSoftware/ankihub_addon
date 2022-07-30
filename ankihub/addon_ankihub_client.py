import json
from json import JSONDecodeError
from pathlib import Path
from pprint import pformat
from typing import Dict

import requests
from aqt import mw
from aqt.utils import tooltip
from requests import Response
from requests.models import HTTPError

from . import LOGGER
from .ankihub_client import AnkiHubClient
from .config import config


class AnkiHubRequestError(Exception):
    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        return (
            f"AnkiHub request error: {self.response.status_code} {self.response.reason}"
        )


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


def authentication_and_exception_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin raise exception hook.")

    treat_404_as_error = getattr(response.request, "treat_404_as_error", True)
    if not treat_404_as_error and response.status_code == 404:
        return response

    if maybe_handle_sign_in(response):
        return response

    if maybe_handle_sign_out(response):
        return response

    try:
        response.raise_for_status()
    except HTTPError:
        LOGGER.exception("raise_exception_hook raises AnkiHubRequestError.")
        raise AnkiHubRequestError(response)

    return response


def maybe_handle_sign_in(response: Response) -> bool:
    # Returns whether this function handled the response.
    LOGGER.debug("Begin sign in handler.")

    if response.status_code == 401 and response.json()["detail"] == "Invalid token.":
        # invalid token
        config.save_token("")
        from .gui.menu import AnkiHubLogin

        mw.taskman.run_on_main(AnkiHubLogin.display_login)
        return True
    elif "/login/" in response.url and response.status_code != 200:
        # wrong credentials
        config.save_token("")
        from .gui.menu import AnkiHubLogin

        mw.taskman.run_on_main(AnkiHubLogin.display_login)
        return True

    elif "/login/" in response.url and response.status_code == 200:
        # correct credentials
        data = response.json()
        token = data.get("token")
        body = response.request.body
        body_dict: Dict = json.loads(body) if body else body
        username = body_dict.get("username")
        config.save_token(token)
        config.save_user_email(username)

        mw.taskman.run_on_main(lambda: tooltip("Signed into AnkiHub!", parent=mw))
        return True
    else:
        return False


def maybe_handle_sign_out(response: Response) -> bool:
    # Returns whether this function handled the response.
    LOGGER.debug("Begin sign out handler.")

    if "/logout/" not in response.url or response.status_code != 204:
        return False

    config.save_token("")
    mw.taskman.run_on_main(lambda: tooltip("Signed out of AnkiHub!", parent=mw))
    return True


DEFAULT_RESPONSE_HOOKS = [
    logging_hook,
    authentication_and_exception_hook,
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
        return s3_response
