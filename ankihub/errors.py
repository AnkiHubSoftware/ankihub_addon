import os
import re
import sys
import traceback
from types import TracebackType
from typing import Type

from aqt import mw
from aqt.gui_hooks import main_window_did_init
from aqt.utils import tooltip
from requests.exceptions import ConnectionError

from . import LOGGER
from .addon_ankihub_client import AnkiHubRequestError
from .config import config
from .constants import ANKIWEB_ID
from .error_reporting import report_exception_and_upload_logs
from .gui.error_feedback import ErrorFeedbackDialog
from .gui.menu import AnkiHubLogin


def handle_exception(
    exc_type: Type[BaseException], exc: BaseException, tb: TracebackType
) -> bool:
    # returns True if the exception was handled in such a way that it doesn't need to be handled further

    LOGGER.debug(
        f"From handle_exception:\n{''.join(traceback.format_exception(exc_type, value=exc, tb=tb))}"
    )

    if not this_addon_is_involved(tb):
        return False

    if isinstance(exc, AnkiHubRequestError):
        if maybe_handle_ankihub_request_error(exc):
            return True

    if isinstance(exc, ConnectionError):
        tooltip("AnkiHub: Could not connect to the internet.", parent=mw)
        return True

    if not should_report_error():
        return False

    context = None
    if isinstance(exc, AnkiHubRequestError):
        context = {
            "reason": exc.response.reason,
            "content": exc.response.content,
        }
    sentry_id = report_exception_and_upload_logs(exception=exc, context=context)
    ErrorFeedbackDialog(exception=exc, event_id=sentry_id)
    return False


def this_addon_is_involved(tb) -> bool:
    tb_str = "".join(traceback.format_tb(tb))
    result = (
        ANKIWEB_ID is not None
        and re.search(rf"/addons21/(ankihub|{ANKIWEB_ID})/", tb_str)
    ) or (ANKIWEB_ID is None and re.search(r"/addons21/ankihub", tb_str))
    return bool(result)


def should_report_error() -> bool:
    result = bool(
        config.public_config.get("report_errors")
        and not os.getenv("REPORT_ERRORS", None) == "0"
    )
    return result


def maybe_handle_ankihub_request_error(error: AnkiHubRequestError) -> bool:
    response = error.response
    if response.status_code == 401 and response.json()["detail"] == "Invalid token.":
        # invalid token
        config.save_token("")
        AnkiHubLogin.display_login()
        return True
    return False


def overwrite_excepthook():
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


def setup_error_handler():
    main_window_did_init.append(overwrite_excepthook)
