"""Error handling and reporting."""
import dataclasses
import os
import re
import sys
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import Future
from pathlib import Path
from sqlite3 import OperationalError
from types import TracebackType
from typing import Any, Callable, Dict, Optional, Type

import aqt
import sentry_sdk
from anki.errors import BackendIOError, DBError, SyncError
from anki.utils import checksum, is_win
from aqt.utils import askUser, showInfo
from requests import exceptions
from sentry_sdk import capture_exception, push_scope
from sentry_sdk.integrations.argv import ArgvIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from ..db import (
    detach_ankihub_db_from_anki_db_connection,
    is_ankihub_db_attached_to_anki_db,
)
from ..settings import (
    ADDON_VERSION,
    ANKI_VERSION,
    ANKIWEB_ID,
    config,
    log_file_path,
    user_files_path,
)
from .error_dialog import ErrorDialog
from .sync import NotLoggedInError
from .utils import (
    check_and_prompt_for_updates_on_main_window,
    show_error_dialog,
    show_tooltip,
)

SENTRY_ENV = "anki_desktop"
os.environ["SENTRY_ENVIRONMENT"] = SENTRY_ENV

# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = ADDON_VERSION

OUTDATED_CLIENT_ERROR_REASON = "Outdated client, please update the AnkiHub add-on."


def setup_error_handler():
    """Set up centralized exception handling and initialize Sentry."""

    _setup_excepthook()
    LOGGER.info("Set up excepthook.")

    if _error_reporting_enabled():
        _initialize_sentry()
        LOGGER.info("Sentry initialized.")
    else:
        LOGGER.info("Error reporting is disabled.")


def report_exception_and_upload_logs(
    exception: BaseException, context: Dict[str, Any] = {}
) -> Optional[str]:
    """Report the exception to Sentry and upload the logs.
    Returns the Sentry event ID."""
    if not _error_reporting_enabled():
        LOGGER.info("Not reporting exception because error reporting is disabled.")
        return None

    logs_key = upload_logs_in_background()
    sentry_id = _report_exception(
        exception=exception, context={**context, "logs": {"filename": logs_key}}
    )
    return sentry_id


def upload_logs_in_background(
    on_done: Optional[Callable[[Future], None]] = None, hide_username=False
) -> str:
    """Upload the logs to S3 in the background.
    Returns the S3 key of the uploaded logs."""

    LOGGER.info("Uploading logs...")

    # many users use their email address as their username and may not want to share it on a forum
    user_name = config.user() if not hide_username else checksum(config.user())[:5]
    key = f"ankihub_addon_logs_{user_name}_{int(time.time())}.log"

    if on_done is not None:
        aqt.mw.taskman.run_in_background(
            task=lambda: _upload_logs(key), on_done=on_done
        )
    else:
        aqt.mw.taskman.run_in_background(
            task=lambda: _upload_logs(key), on_done=_on_upload_logs_done
        )

    return key


def upload_data_dir_and_logs_in_background(
    on_done: Callable[[Future], None] = None
) -> str:
    """Upload the data dir and logs to S3 in the background.
    Returns the S3 key of the uploaded file."""

    LOGGER.info("Uploading data dir and logs...")

    # many users use their email address as their username and may not want to share it on a forum
    user_name_hash = checksum(config.user())[:5]
    key = f"ankihub_addon_debug_info_{user_name_hash}_{int(time.time())}.zip"

    aqt.mw.taskman.run_in_background(
        task=lambda: _upload_data_dir_and_logs(key), on_done=on_done
    )

    return key


def _upload_data_dir_and_logs(key: str) -> str:

    # detach the ankihub database from the anki database connection to prevent file permission errors
    detach_ankihub_db_from_anki_db_connection()

    # zip the user files and the log file
    file_path = _zip_data_dir_and_logs()

    # upload the zip file
    try:
        client = AnkiHubClient()
        client.upload_logs(
            file=file_path,
            key=key,
        )
        LOGGER.info("Data dir and logs uploaded.")
        return key
    finally:
        os.unlink(file_path)


def _zip_data_dir_and_logs() -> Path:
    """Zip the user files directory and the log file and return the path of the zip file."""
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    with zipfile.ZipFile(temp_file.name, "w") as zipf:
        source_dir = user_files_path()
        for file in source_dir.rglob("*"):
            # previously logs were stored in the user files directory and we don't want to
            # include old log files in the zip
            if file.name.endswith(".log") or ".log." in file.name:
                continue
            zipf.write(file, arcname=file.relative_to(source_dir))

        log_file = log_file_path()
        if log_file.exists():
            zipf.write(log_file, arcname=log_file.name)
        else:
            LOGGER.info("No logs to upload.")

    return Path(temp_file.name)


def _setup_excepthook():
    """Set up centralized exception handling.
    Exceptions are are either handled by our exception handler or passed to the original excepthook
    which opens Anki's error dialog.
    If error reporting is enabled, unhandled exceptions (in which the ankihub add-on is innvolved)
    are reported to Sentry and the user is prompted to provide feedback (in addition to Anki's error dialog opening).
    """

    def excepthook(
        etype: Type[BaseException], val: BaseException, tb: Optional[TracebackType]
    ) -> Any:
        LOGGER.info(
            f"This addon is mentioned in traceback: {_this_addon_mentioned_in_tb(tb)}",
        )

        handled = False
        try:
            handled = _try_handle_exception(exc_type=etype, exc_value=val, tb=tb)
        except Exception as e:
            # catching all exceptions here prevents a potential exception loop
            LOGGER.exception("The exception handler threw an exception.", exc_info=e)
        finally:
            if handled:
                return

            try:
                _maybe_report_exception_and_show_feedback_dialog(exception=val)
            except Exception as e:
                LOGGER.warning(
                    "There was an error while reporting the exception or showing the feedback dialog.",
                    exc_info=e,
                )

    sys.excepthook = excepthook


def _maybe_report_exception_and_show_feedback_dialog(exception: BaseException) -> None:
    sentry_event_id: Optional[str] = None
    if _error_reporting_enabled():
        sentry_event_id = report_exception_and_upload_logs(exception=exception)

    if _this_addon_mentioned_in_tb(exception.__traceback__):
        ErrorDialog(exception, sentry_event_id=sentry_event_id).exec()


def _try_handle_exception(
    exc_type: Type[BaseException], exc_value: BaseException, tb: Optional[TracebackType]
) -> bool:
    """Try to handle the exception. Return True if the exception was handled, False otherwise."""
    LOGGER.info(
        f"From _try_handle_exception:\n{''.join(traceback.format_exception(exc_type, value=exc_value, tb=tb))}"
    )

    if isinstance(exc_value, AnkiHubHTTPError):
        if _maybe_handle_ankihub_http_error(exc_value):
            LOGGER.info("AnkiHubRequestError was handled.")
            return True

    if isinstance(exc_value, AnkiHubRequestException):
        if isinstance(
            exc_value.original_exception, (exceptions.ConnectionError, ConnectionError)
        ):
            if "[Errno -2] Name or service not known" in str(
                exc_value.original_exception
            ):
                show_tooltip(
                    "🚧 AnkiHub is undergoing routine maintenance. "
                    "Please visit ankihub.net/status and check your email for details.",
                    period=5000,
                )
            elif "[Errno -3] Temporary failure in name resolution" in str(
                exc_value.original_exception
            ):
                show_tooltip(
                    "🔌 No Internet Connection detected. Please check your internet connection and try again.",
                    period=5000,
                )
            else:
                show_tooltip(
                    "📶 Could not connect to AnkiHub (no internet or the site is down for maintenance)",
                    period=5000,
                )
            return True

    if _is_memory_full_error(exc_value):
        show_error_dialog(
            "Could not finish because your hard drive does not have enough space.",
            title="AnkiHub",
        )
        LOGGER.info("Showing full disk warning.")
        return True

    if isinstance(exc_value, NotLoggedInError):
        from .menu import AnkiHubLogin

        AnkiHubLogin.display_login()
        LOGGER.info("NotLoggedInError was handled.")
        return True

    if (
        isinstance(exc_value, AttributeError)
        and "NoneType" in str(exc_value)
        and "has no attribute" in str(exc_value)
        and "mw.col" in "".join(traceback.format_tb(tb))
        and aqt.mw.col is None
    ):
        # Ignore errors that occur when the collection is None.
        # This can e.g. happen when a background task is running
        # and the user switches to a different Anki profile.
        LOGGER.warning("Collection is None was handled")
        return True

    if isinstance(
        exc_value, OSError
    ) and "Could not find a suitable TLS CA certificate bundle" in str(exc_value):
        showInfo("Please restart Anki.", title="AnkiHub")
        LOGGER.warning("TLS CA certificate bundle error was handled", exc_value)
        return True

    return False


def _maybe_handle_ankihub_http_error(error: AnkiHubHTTPError) -> bool:
    """Return True if the error was handled, False otherwise."""
    response = error.response
    if response.status_code == 401:
        config.save_token("")
        from .menu import AnkiHubLogin

        AnkiHubLogin.display_login()
        return True
    elif (
        response.status_code == 406 and response.reason == OUTDATED_CLIENT_ERROR_REASON
    ):
        if askUser(
            "The AnkiHub add-on needs to be updated to continue working.<br>"
            "Do you want to open the add-on update dialog now?"
        ):
            check_and_prompt_for_updates_on_main_window()
        return True
    elif response.status_code == 403:
        response_data = response.json()
        error_message = response_data.get("detail")
        if error_message:
            show_error_dialog(error_message, title="Oh no!")
            return True

    return False


def _is_memory_full_error(exc_value: BaseException) -> bool:
    result = (
        (isinstance(exc_value, DBError) and "is full" in str(exc_value).lower())
        or (
            isinstance(exc_value, BackendIOError)
            and "not enough space" in str(exc_value).lower()
        )
        or (
            isinstance(exc_value, BackendIOError)
            and "not enough memory" in str(exc_value).lower()
        )
        or (
            isinstance(exc_value, BackendIOError)
            and "no space left" in str(exc_value).lower()
        )
        or (
            isinstance(exc_value, OSError) and "no space left" in str(exc_value).lower()
        )
        or (
            isinstance(exc_value, SyncError)
            and "no space left" in str(exc_value).lower()
        )
        or (
            isinstance(exc_value, OperationalError)
            and "database or disk is full" in str(exc_value).lower()
        )
    )
    return result


def _this_addon_mentioned_in_tb(tb: TracebackType) -> bool:
    tb_str = "".join(traceback.format_tb(tb))
    result = _contains_path_to_this_addon(tb_str)
    return result


def _contains_path_to_this_addon(tb_str: str) -> bool:
    result = (
        ANKIWEB_ID is not None
        and re.search(rf"(/|\\)addons21(/|\\)(ankihub|{ANKIWEB_ID})(/|\\)", tb_str)
    ) or (ANKIWEB_ID is None and re.search(r"(/|\\)addons21(/|\\)ankihub", tb_str))
    return bool(result)


def _initialize_sentry():
    sentry_sdk.init(
        dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
        traces_sample_rate=1.0,
        release=ADDON_VERSION,
        environment=SENTRY_ENV,
        # We probably want most default integrations, but we don't want e.g. the ExcepthookIntegration
        # because we set up our own excepthook.
        default_integrations=False,
        integrations=[
            ArgvIntegration(),
            DedupeIntegration(),
            LoggingIntegration(),
            StdlibIntegration(),
            ThreadingIntegration(),
        ],
        # This disable the AtexitIntegration because it causes a RuntimeError when Anki is closed.
        shutdown_timeout=0,
    )


def _report_exception(
    exception: BaseException, context: Dict[str, Dict[str, Any]] = {}
) -> Optional[str]:
    """Report an exception to Sentry."""
    if not _error_reporting_enabled():
        return None

    with push_scope() as scope:
        scope.level = "error"
        scope.user = {"id": config.user()}
        scope.set_context("add-on config", dataclasses.asdict(config._private_config))
        scope.set_context("addon version", {"version": ADDON_VERSION})
        scope.set_context("anki version", {"version": ANKI_VERSION})

        try:
            scope.set_context("user files", _user_files_context_dict())
        except Exception as e:
            LOGGER.warning(f"Could not get user files context: {e}")

        for key, value in context.items():
            scope.set_context(key, value)

        if exception.__traceback__:
            scope.set_tag(
                "ankihub_in_traceback",
                str(_this_addon_mentioned_in_tb(exception.__traceback__)),
            )
        else:
            LOGGER.warning("Exception has no traceback.")

        if isinstance(exception, AnkiHubHTTPError):
            scope.set_context(
                "response",
                {
                    "url": exception.response.url,
                    "reason": exception.response.reason,
                    "content": exception.response.content,
                },
            )
            # The url in the fingerprint is normalized so that sentry can group errors by url in a
            # more meaningful way.
            scope.fingerprint = [
                "{{ default }}",
                _normalize_url(exception.response.url),
            ]

            scope.set_context(
                "request",
                {
                    "method": exception.response.request.method,
                    "url": exception.response.request.url,
                    "body": exception.response.request.body,
                },
            )

        sentry_id = capture_exception(exception)

    return sentry_id


def _user_files_context_dict() -> Dict[str, Any]:
    """Return a dict with information about the user files of the AnkiHub add-on to be sent as
    context to Sentry."""
    ankihub_module = aqt.mw.addonManager.addonFromModule(__name__)
    user_files_path = Path(aqt.mw.addonManager._userFilesPath(ankihub_module))
    all_file_paths = [user_files_path, *list(user_files_path.rglob("*"))]
    problematic_file_paths = []
    if is_win:
        problematic_file_paths = [
            file for file in all_file_paths if not _file_is_accessible(file)
        ]

    result = {
        "all files": [str(file) for file in all_file_paths],
        "inaccessible files (Windows)": [str(file) for file in problematic_file_paths],
        "Is ankihub database attached to anki database?": is_ankihub_db_attached_to_anki_db(),
    }
    return result


def _file_is_accessible(f: Path) -> bool:
    # Only works on Windows.
    # Checks if a file is accessible (and not e.g. open by another process) by trying to rename it to itself.
    # See https://stackoverflow.com/a/37256114.
    assert is_win
    try:
        os.rename(f, f)
    except (OSError, PermissionError):
        return False
    else:
        return True


def _normalize_url(url: str):

    # remove parameters
    result = re.sub(r"\?.+$", "", url)

    # replace ids with placeholder
    uuid_re = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    result = re.sub(rf"/({uuid_re}|[0-9]+)", "/<id>", result)
    return result


def _error_reporting_enabled() -> bool:

    # Not sure if this is really necessary, but HyperTTS does it:
    # https://github.com/Language-Tools/anki-hyper-tts/blob/a73785eb43068db08f073809c74c4dd1f236a557/__init__.py#L34-L37
    # Some other add-ons package an obsolete version of sentry-sdk, which causes problems(?) when
    # using sentry_sdk. We therefore check the version here and disable error reporting if the
    # version is too old to avoid problems.
    if obsolete_version_of_sentry_sdk():
        LOGGER.info(
            "Obsolete version of sentry-sdk detected. Error reporting disabled."
        )
        return False

    result = (
        config.public_config.get("report_errors")
        and not os.getenv("REPORT_ERRORS", None) == "0"
    )
    return result


def obsolete_version_of_sentry_sdk() -> bool:
    result = [int(x) for x in sentry_sdk.VERSION.split(".")] < [1, 5, 5]
    return result


def _upload_logs(key: str) -> str:
    if not log_file_path().exists():
        LOGGER.info("No logs to upload.")
        return None

    try:
        client = AnkiHubClient()
        client.upload_logs(
            file=log_file_path(),
            key=key,
        )
        LOGGER.info("Logs uploaded.")
        return key
    except AnkiHubHTTPError as e:
        LOGGER.info("Logs upload failed.")
        raise e


def _on_upload_logs_done(future: Future) -> None:
    try:
        future.result()
    except AnkiHubHTTPError as e:
        from .errors import OUTDATED_CLIENT_ERROR_REASON

        # Don't report outdated client errors that happen when uploading logs,
        # because they are handled by the add-on when they happen in other places
        # and we don't want to see them in Sentry.
        if e.response.status_code == 401 or (
            e.response.status_code == 406
            and e.response.reason == OUTDATED_CLIENT_ERROR_REASON
        ):
            return
        _report_exception(e)
    except Exception as e:
        _report_exception(e)