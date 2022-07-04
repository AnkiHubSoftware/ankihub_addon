import json
from json import JSONDecodeError
from pprint import pformat
from typing import Dict

from aqt import mw
from aqt.utils import showText, tooltip
from requests import Response
from requests.models import HTTPError

from . import LOGGER, report_exception
from .ankihub_client import AnkiHubClient
from .config import config
from .messages import messages


def show_anki_message_hook(response: Response, *args, **kwargs):
    LOGGER.debug("Begin show anki message hook.")
    endpoint = response.request.url

    def message():
        showText(messages.request_error(), type="html")

    if response.status_code > 299 and "/logout/" not in endpoint:
        mw.taskman.run_on_main(message)
    return response


def logging_hook(response: Response, *args, **kwargs):
    endpoint = response.request.url
    method = response.request.method
    body = response.request.body
    body = json.loads(body) if body else body
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
    try:
        response.raise_for_status()
    except HTTPError:
        report_exception()


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

        def open_login_dialog():
            if not AnkiHubLogin.is_shown():
                AnkiHubLogin.display_login()

        mw.taskman.run_on_main(open_login_dialog)

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
