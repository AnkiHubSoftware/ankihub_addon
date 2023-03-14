import os
import re
import sys
import traceback
from types import TracebackType
from typing import Any, Optional, Type

import aqt
from anki.errors import BackendIOError, DBError, SyncError
from aqt.utils import askUser, showText, showWarning, tooltip
from requests import exceptions

from . import LOGGER
from .addon_ankihub_client import AnkiHubRequestError
from .error_reporting import report_exception_and_upload_logs
from .gui.error_feedback import ErrorFeedbackDialog
from .gui.menu import AnkiHubLogin
from .gui.utils import check_and_prompt_for_updates_on_main_window
from .settings import ANKIWEB_ID, config
from .sync import NotLoggedInError


def handle_exception(
    exc_type: Type[BaseException], exc: BaseException, tb: TracebackType
) -> bool:
    # returns True if the exception was handled in such a way that it doesn't need to be handled further

    LOGGER.info(
        f"From handle_exception:\n{''.join(traceback.format_exception(exc_type, value=exc, tb=tb))}"
    )

    if not this_addon_is_involved(tb):
        LOGGER.info("This addon is not involved.")
        return False

    if isinstance(exc, AnkiHubRequestError):
        if maybe_handle_ankihub_request_error(exc):
            LOGGER.info("AnkiHubRequestError was handled.")
            return True

        try:
            response_data = exc.response.json()
            details = (
                response_data.get("detail")
                or response_data.get("details")
                or response_data.get("errors")
            )
        except:
            details = None

        if details:
            showText(f"Error while communicating with AnkiHub:\n{details}")

    if isinstance(exc, (exceptions.ConnectionError, ConnectionError)):
        tooltip(
            "Could not connect to AnkiHub (no internet or the site is down for maintenance)",
            parent=aqt.mw,
        )
        return True

    if (
        (isinstance(exc, DBError) and "is full" in str(exc).lower())
        or (isinstance(exc, BackendIOError) and "not enough space" in str(exc).lower())
        or (isinstance(exc, BackendIOError) and "not enough memory" in str(exc).lower())
        or (isinstance(exc, BackendIOError) and "no space left" in str(exc).lower())
        or (isinstance(exc, OSError) and "no space left" in str(exc).lower())
        or (isinstance(exc, SyncError) and "no space left" in str(exc).lower())
    ):
        showWarning(
            "Could not finish because your hard drive does not have enough space.",
            title="AnkiHub",
        )
        LOGGER.info("Showing full disk warning.")
        return True

    if isinstance(exc, NotLoggedInError):
        AnkiHubLogin.display_login()
        LOGGER.info("NotLoggedInError was handled.")
        return True

    if not should_report_error():
        LOGGER.info("Reporting errors is disabled.")
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
        and response.reason == "Outdated client, please update the AnkiHub add-on."
    ):
        if askUser(
            "The AnkiHub add-on needs to be updated to continue working.<br>"
            "Do you want to open the add-on update dialog now?"
        ):
            check_and_prompt_for_updates_on_main_window()
        return True
    return False


def setup_error_handler():
    def excepthook(
        etype: Type[BaseException], val: BaseException, tb: Optional[TracebackType]
    ) -> Any:
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
