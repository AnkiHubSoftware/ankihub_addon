import os
import re
import sys
import traceback
from types import TracebackType
from typing import Type

from anki.errors import BackendIOError, DBError
from aqt import mw
from aqt.utils import showWarning, tooltip
from requests.exceptions import ConnectionError

from . import LOGGER
from .addon_ankihub_client import AnkiHubRequestError
from .error_reporting import report_exception_and_upload_logs
from .gui.error_feedback import ErrorFeedbackDialog
from .gui.menu import AnkiHubLogin
from .settings import ANKIWEB_ID, config


def handle_exception(
    exc_type: Type[BaseException], exc: BaseException, tb: TracebackType
) -> bool:
    # returns True if the exception was handled in such a way that it doesn't need to be handled further

    LOGGER.debug(
        f"From handle_exception:\n{''.join(traceback.format_exception(exc_type, value=exc, tb=tb))}"
    )

    if not this_addon_is_involved(tb):
        LOGGER.debug("This addon is not involved.")
        return False

    if isinstance(exc, AnkiHubRequestError):
        if maybe_handle_ankihub_request_error(exc):
            LOGGER.debug("AnkiHubRequestError was handled.")
            return True

    if isinstance(exc, ConnectionError):
        tooltip("AnkiHub: Could not connect to the internet.", parent=mw)
        return True

    if (isinstance(exc, DBError) and "is full" in str(exc).lower()) or (
        isinstance(exc, BackendIOError) and "no space left" in str(exc).lower()
    ):
        showWarning(
            "Could not finish because your hard drive is full.", title="AnkiHub"
        )
        LOGGER.debug("Showing full disk warning.")
        return True

    if not should_report_error():
        LOGGER.debug("Reporting errors is disabled.")
        return False

    sentry_id = report_exception_and_upload_logs(exception=exc)
    ErrorFeedbackDialog(exception=exc, event_id=sentry_id)
    return False


def this_addon_is_involved(tb) -> bool:
    tb_str = "".join(traceback.format_tb(tb))
    result = (
        ANKIWEB_ID is not None
        and re.search(rf"(/|\\)addons21(/|\\)(ankihub|{ANKIWEB_ID})(/|\\)", tb_str)
    ) or (ANKIWEB_ID is None and re.search(r"(/|\\)addons21(/|\\)ankihub", tb_str))
    return bool(result)


def should_report_error() -> bool:
    result = bool(
        config.public_config.get("report_errors")
        and not os.getenv("REPORT_ERRORS", None) == "0"
    )
    return result


def maybe_handle_ankihub_request_error(error: AnkiHubRequestError) -> bool:
    response = error.response
    if response.status_code == 401:
        config.save_token("")
        AnkiHubLogin.display_login()
        return True
    elif (
        response.status_code == 406
        and response.reason == "Outdated client, please update"
    ):
        showWarning(
            "Please update the AnkiHub add-on to the latest version.",
            title="AnkiHub",
        )
        return True
    return False


def setup_error_handler():
    def excepthook(
        etype: Type[BaseException], val: Exception, tb: TracebackType
    ) -> None:
        handled = False
        try:
            handled = handle_exception(exc_type=etype, exc=val, tb=tb)
        except Exception:
            # catching all exceptions here prevents an potential exception loop
            LOGGER.exception("handle_exception threw an exception.")
        finally:
            if not handled:
                original_except_hook(etype, val, tb)

    original_except_hook = sys.excepthook
    sys.excepthook = excepthook
