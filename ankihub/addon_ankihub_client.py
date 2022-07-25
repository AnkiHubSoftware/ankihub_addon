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
from .gui.error_feedback import ErrorFeedbackDialog


def show_anki_message_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin show anki message hook.")
    endpoint = response.request.url
    sentry_event_id: str = getattr(response, "sentry_event_id", None)

    treat_404_as_error = getattr(response.request, "treat_404_as_error", True)
    if not treat_404_as_error and response.status_code == 404:
        return response

    def message():
        ErrorFeedbackDialog(sentry_event_id)

    if (
        response.status_code > 299
        and "/logout/" not in endpoint
        and sentry_event_id is not None
    ):
        mw.taskman.run_on_main(message)
    return response


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


def report_exception_hook(response: Response, *args, **kwargs):
    from .error_reporting import report_exception_and_upload_logs

    LOGGER.debug("Begin report exception hook.")
    if "pre-signed-url" in response.url and "addon_logs" in response.url:
        LOGGER.debug("can't send logs due to invalid credentials")
        return response

    treat_404_as_error = getattr(response.request, "treat_404_as_error", True)
    if not treat_404_as_error and response.status_code == 404:
        return response

    try:
        response.raise_for_status()
    except HTTPError:
        ctx = {"response": {"reason": response.reason, "content": response.text}}
        event_id = report_exception_and_upload_logs(context=ctx)
        response.sentry_event_id = event_id  # type: ignore

    return response


def sign_in_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin sign in hook.")
    if response.status_code == 401 and response.json()["detail"] == "Invalid token.":
        config.save_token("")
        from .gui.menu import AnkiHubLogin

        mw.taskman.run_on_main(AnkiHubLogin.display_login)
        return response
    elif "/login/" in response.url and response.status_code != 200:
        config.save_token("")
        from .gui.menu import AnkiHubLogin

        mw.taskman.run_on_main(AnkiHubLogin.display_login)

        return response
    elif "/login/" in response.url and response.status_code == 200:
        data = response.json()
        token = data.get("token")
        body = response.request.body
        body_dict: Dict = json.loads(body) if body else body
        username = body_dict.get("username")
        config.save_token(token)
        config.save_user_email(username)

        mw.taskman.run_on_main(lambda: tooltip("Signed into AnkiHub!", parent=mw))
        return response
    else:
        return response


def sign_out_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin sign out hook.")
    if "/logout/" not in response.url or response.status_code != 204:
        return response

    config.save_token("")
    mw.taskman.run_on_main(lambda: tooltip("Signed out of AnkiHub!", parent=mw))
    return response


DEFAULT_RESPONSE_HOOKS = [
    logging_hook,
    report_exception_hook,
    sign_in_hook,
    show_anki_message_hook,
    sign_out_hook,
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
