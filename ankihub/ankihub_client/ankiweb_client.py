"""AnkiWeb account (signup/login) methods for AnkiHubClient.

Only enabled on Python 3.10+ due to the protobuf-py requirement.
"""

from typing import TYPE_CHECKING

from requests import Response

from ..proto.frontend.account_pb import ResendVerificationRequest, ResendVerificationResponse
from ..proto.user_backend.account_pb import (
    LoginRequest,
    LoginResponse,
    SignupRequest,
    SignupResponse,
)
from ..proto.user_backend.magic_code_pb import (
    MagicCodeLoginRequest,
    MagicCodeLoginResponse,
    MagicCodeLoginVerifyRequest,
    MagicCodeLoginVerifyResponse,
    MagicCodeSignupRequest,
    MagicCodeSignupResponse,
    MagicCodeSignupVerifyRequest,
    MagicCodeSignupVerifyResponse,
)
from .ankihub_client import API


class AnkiWebHTTPError(Exception):
    """An unexpected HTTP code was returned in response to a request by the AnkiWeb client."""

    def __init__(self, response: Response):
        self.response = response

    def __str__(self):
        try:
            return self.response.text
        except Exception:
            return f"AnkiWeb responded with status error code {self.response.status_code}"


class AnkiWebClientMixin:
    """Mixin adding AnkiWeb account signup/login methods to AnkiHubClient."""

    if TYPE_CHECKING:

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
            is_long_running=False,
        ) -> "Response": ...

    def ankiweb_login(self, email: str, password: str) -> LoginResponse:
        data = LoginRequest(email=email, password=password)
        response = self._send_request("post", API.ANKIWEB, "/auth/login", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return LoginResponse.from_binary(response.content)

    def ankiweb_signup(self, email: str, password: str, terms: bool) -> SignupResponse:
        data = SignupRequest(email=email, password=password, terms=terms)
        response = self._send_request("post", API.ANKIWEB, "/auth/signup", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return SignupResponse.from_binary(response.content)

    def ankiweb_request_signup_code(self, email: str, terms: bool) -> MagicCodeSignupResponse:
        data = MagicCodeSignupRequest(email=email, terms=terms)
        response = self._send_request("post", API.ANKIWEB, "/auth/magic-code/signup", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return MagicCodeSignupResponse.from_binary(response.content)

    def ankiweb_verify_signup_code(self, email: str, code: str) -> MagicCodeSignupVerifyResponse:
        data = MagicCodeSignupVerifyRequest(email=email, code=code)
        response = self._send_request("post", API.ANKIWEB, "/auth/magic-code/signup-verify", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return MagicCodeSignupVerifyResponse.from_binary(response.content)

    def ankiweb_request_login_code(self, email: str) -> MagicCodeLoginResponse:
        data = MagicCodeLoginRequest(email=email)
        response = self._send_request("post", API.ANKIWEB, "/auth/magic-code/login", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return MagicCodeLoginResponse.from_binary(response.content)

    def ankiweb_verify_login_code(self, email: str, code: str) -> MagicCodeLoginVerifyResponse:
        data = MagicCodeLoginVerifyRequest(email=email, code=code)
        response = self._send_request("post", API.ANKIWEB, "/auth/magic-code/login-verify", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return MagicCodeLoginVerifyResponse.from_binary(response.content)

    def ankiweb_resend_verification(self, hkey: str) -> ResendVerificationResponse:
        data = ResendVerificationRequest(hkey=hkey)
        response = self._send_request("post", API.ANKIWEB, "/svc/account/resend-verification", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiWebHTTPError(response)
        return ResendVerificationResponse.from_binary(response.content)
