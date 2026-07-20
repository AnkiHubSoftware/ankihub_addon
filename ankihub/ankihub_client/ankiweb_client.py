"""AnkiWeb account (signup/login) methods for AnkiHubClient.

Only enabled on Python 3.10+ due to the protobuf-py requirement.
"""

from typing import TYPE_CHECKING

from ..proto.account_pb import (
    LoginRequest,
    LoginResponse,
    MagicCodeLoginRequest,
    MagicCodeLoginResponse,
    MagicCodeLoginVerifyRequest,
    MagicCodeLoginVerifyResponse,
    MagicCodeSignupRequest,
    MagicCodeSignupResponse,
    MagicCodeSignupVerifyRequest,
    MagicCodeSignupVerifyResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    SignupRequest,
    SignupResponse,
)
from .ankihub_client import API, AnkiHubHTTPError

if TYPE_CHECKING:
    from requests import Response


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

    def ankiweb_login(self, username: str, password: str) -> LoginResponse:
        data = LoginRequest(username=username, password=password)
        response = self._send_request("post", API.ANKIWEB, "/account/login", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return LoginResponse.from_binary(response.content)

    def ankiweb_signup(self, username: str, password: str) -> SignupResponse:
        data = SignupRequest(username=username, password=password)
        response = self._send_request("post", API.ANKIWEB, "/account/signup", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return SignupResponse.from_binary(response.content)

    def ankiweb_request_signup_code(self, email: str, terms: bool) -> MagicCodeSignupResponse:
        data = MagicCodeSignupRequest(email=email, terms=terms)
        response = self._send_request("post", API.ANKIWEB, "/account/magic-code-signup", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return MagicCodeSignupResponse.from_binary(response.content)

    def ankiweb_verify_signup_code(self, email: str, code: str) -> MagicCodeSignupVerifyResponse:
        data = MagicCodeSignupVerifyRequest(email=email, code=code)
        response = self._send_request("post", API.ANKIWEB, "/account/magic-code-signup-verify", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return MagicCodeSignupVerifyResponse.from_binary(response.content)

    def ankiweb_request_login_code(self, email: str) -> MagicCodeLoginResponse:
        data = MagicCodeLoginRequest(email=email)
        response = self._send_request("post", API.ANKIWEB, "/account/magic-code-login", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return MagicCodeLoginResponse.from_binary(response.content)

    def ankiweb_verify_login_code(self, email: str, code: str) -> MagicCodeLoginVerifyResponse:
        data = MagicCodeLoginVerifyRequest(email=email, code=code)
        response = self._send_request("post", API.ANKIWEB, "/account/magic-code-login-verify", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return MagicCodeLoginVerifyResponse.from_binary(response.content)

    # FIXME: this only works with cookie authentication
    def ankiweb_resend_verification(self) -> ResendVerificationResponse:
        data = ResendVerificationRequest()
        response = self._send_request("post", API.ANKIWEB, "/account/resend-verification", data=data.to_binary())
        if response.status_code != 200:
            raise AnkiHubHTTPError(response)
        return ResendVerificationResponse.from_binary(response.content)
