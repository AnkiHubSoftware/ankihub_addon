import json
from json import JSONDecodeError
from pprint import pformat
from typing import Dict

from aqt.utils import showText
from requests import Response

from . import LOGGER
from .ankihub_client import AnkiHubClient
from .config import config
from .constants import USER_SUPPORT_EMAIL_SLUG


def show_anki_message_hook(response: Response, *args, **kwargs):
    endpoint = response.request.url
    if response.status_code > 299 and "/logout/" not in endpoint:
        showText(
            "Uh oh! There was a problem with your request.\n\n"
            "If you haven't already signed in using the AnkiHub menu please do so. "
            "Make sure your username and password are correct and that you have "
            "confirmed your AnkiHub account through email verification. If you "
            "believe this is an error, please reach out to user support at "
            f"{USER_SUPPORT_EMAIL_SLUG}. This error will be automatically reported."
        )
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


def sign_in_hook(response: Response, *args, **kwargs):
    if "/login/" not in response.url or response.status_code != 200:
        return

    data = response.json()
    token = data.get("token")
    body = response.request.body
    body_dict: Dict = json.loads(body) if body else body
    username = body_dict.get("username")
    if token:
        config.save_token(token)
        config.save_user_email(username)


def sign_out_hook(response: Response, *args, **kwargs):
    if "/logout/" not in response.url or response.status_code != 204:
        return

    config.save_token("")
    LOGGER.debug("Token cleared from config.")


DEFAULT_RESPONSE_HOOKS = [
    logging_hook,
    show_anki_message_hook,
    sign_in_hook,
    sign_out_hook,
]


class AddonAnkiHubClient(AnkiHubClient):
    def __init__(self, send_request=True, hooks=None) -> None:
        super().__init__(
            send_request,
            hooks=hooks if hooks is not None else DEFAULT_RESPONSE_HOOKS,
            token=config.private_config.token,
        )
