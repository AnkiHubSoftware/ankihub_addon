"""Error handling and reporting."""

import dataclasses
import os
import re
import socket
import sys
import tempfile
import time
import traceback
import zipfile
from json import JSONDecodeError
from pathlib import Path
from sqlite3 import OperationalError
from textwrap import dedent
from types import TracebackType
from typing import Any, Callable, Dict, Optional, Type

import aqt
import sentry_sdk
from anki.errors import BackendIOError, DBError, SyncError
from anki.utils import checksum, is_win
from aqt.utils import showInfo
from requests import ReadTimeout, exceptions
from sentry_sdk import capture_exception, push_scope
from sentry_sdk.integrations.argv import ArgvIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubHTTPError, AnkiHubRequestException
from ..db.exceptions import MissingValueError
from ..gui.exceptions import DeckDownloadAndInstallError, FullSyncCancelled
from ..gui.terms_dialog import TermsAndConditionsDialog
from ..settings import (
    ADDON_VERSION,
    ANKI_VERSION,
    ANKIWEB_ID,
    addon_dir_path,
    ankihub_base_path,
    config,
    log_file_path,
)
from .deck_updater import NotLoggedInError
from .error_dialog import ErrorDialog
from .operations import AddonQueryOp
from .utils import (
    ask_user,
    check_and_prompt_for_updates_on_main_window,
    run_with_delay_when_progress_dialog_is_open,
    show_error_dialog,
    show_tooltip,
)

SENTRY_ENV = "anki_desktop"
os.environ["SENTRY_ENVIRONMENT"] = SENTRY_ENV

# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = ADDON_VERSION

OUTDATED_CLIENT_RESPONSE_DETAIL = "Outdated client"
TERMS_AGREEMENT_NOT_ACCEPTED_DETAIL = (
    "You need to accept the terms and conditions to perform this action."
)


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
    on_done: Optional[Callable[[str], None]] = None, hide_username=False
) -> str:
    """Upload the logs to S3 in the background.
    Returns the S3 key of the uploaded logs."""

    LOGGER.info("Uploading logs...")

    key = f"ankihub_addon_logs_{_username_or_hash(hide_username=hide_username)}_{int(time.time())}.log"

    op = (
        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: _upload_logs(key),
            success=on_done if on_done is not None else lambda _: None,
        )
        .failure(_on_upload_logs_failure)
        .without_collection()
    )
    aqt.mw.taskman.run_on_main(op.run_in_background)

    return key


def upload_logs_and_data_in_background(
    on_done: Optional[Callable[[str], None]] = None,
) -> str:
    """Upload the data dir and logs to S3 in the background.
    Returns the S3 key of the uploaded file."""

    LOGGER.info("Uploading data dir and logs...")

    # many users use their email address as their username and may not want to share it on a forum
    key = f"ankihub_addon_debug_info_{_username_or_hash(hide_username=True)}_{int(time.time())}.zip"

    op = (
        AddonQueryOp(
            parent=aqt.mw,
            op=lambda _: _upload_logs_and_data_in_background(key),
            success=on_done if on_done is not None else lambda _: None,
        )
        .failure(_on_upload_logs_failure)
        .without_collection()
    )
    aqt.mw.taskman.run_on_main(op.run_in_background)

    return key


def _username_or_hash(hide_username: bool) -> str:
    if config.user():
        if config.username():
            return config.username()
        # Many users use their email address for login and may not want to share it on a forum
        return checksum(config.user())[:5] if hide_username else config.user()
    else:
        return "not_signed_in"


def _upload_logs_and_data_in_background(key: str) -> str:
    file_path = _zip_logs_and_data()

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


def _zip_logs_and_data() -> Path:
    """Zip the ankihub base directory (which contains logs) and the anki collection.
    Return the path of the zip file."""
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    with zipfile.ZipFile(temp_file.name, "w") as zipf:
        # Add the ankihub base directory to the zip. It also contains the logs.
        source_dir = ankihub_base_path()
        for file in source_dir.rglob("*"):
            zipf.write(file, arcname=file.relative_to(source_dir))

        # Add the Anki collection to the zip.
        try:
            zipf.write(Path(aqt.mw.col.path), arcname="collection.anki2")
        except Exception as e:
            LOGGER.warning("Could not add Anki collection to zip.", exc_info=e)

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
            "Excepthook",
            addon_mentioned_in_traceback=_this_addon_mentioned_in_tb(tb),
        )

        handled = False
        try:
            handled = _try_handle_exception(exc_value=val, tb=tb)
        except Exception as e:
            # catching all exceptions here prevents a potential exception loop
            LOGGER.exception("The exception handler threw an exception.", exc_info=e)
        finally:
            if handled:
                return

            if _this_addon_mentioned_in_tb(tb):
                try:
                    _show_feedback_dialog_and_maybe_report_exception(exception=val)
                except Exception as e:
                    LOGGER.warning(
                        "There was an error while reporting the exception or showing the feedback dialog.",
                        exc_info=e,
                    )
            else:
                original_excepthook(etype, val, tb)

    original_excepthook = sys.excepthook
    sys.excepthook = excepthook


def _show_feedback_dialog_and_maybe_report_exception(exception: BaseException) -> None:
    sentry_event_id: Optional[str] = None
    if _error_reporting_enabled():
        sentry_event_id = report_exception_and_upload_logs(exception=exception)

    ErrorDialog(exception, sentry_event_id=sentry_event_id).exec()


def _try_handle_exception(
    exc_value: BaseException, tb: Optional[TracebackType]
) -> bool:
    """Try to handle the exception. Return True if the exception was handled, False otherwise."""
    LOGGER.info("Trying to handle exception...", exc_info=exc_value)

    if not addon_dir_path().exists():
        show_error_dialog(
            dedent(
                """
                The AnkiHub add-on directory cannot be found.<br>
                If you've uninstalled the add-on, please restart Anki.<br>
                If you're facing issues, please reinstall the add-on.
                """
            ).strip("\n"),
            title="AnkiHub",
        )
        LOGGER.info("Showing add-on directory not found warning.")
        return True

    if isinstance(exc_value, (DeckDownloadAndInstallError, AnkiHubRequestException)):
        exc_value = exc_value.original_exception

    if isinstance(exc_value, FullSyncCancelled):
        show_tooltip("AnkiHub sync cancelled")
        LOGGER.info("FullSyncCancelled was handled.")
        return True

    if isinstance(exc_value, AnkiHubHTTPError):
        if _maybe_handle_ankihub_http_error(exc_value):
            LOGGER.info("AnkiHubRequestError was handled.")
            return True

    if isinstance(
        exc_value, (exceptions.ConnectionError, ConnectionError, ReadTimeout)
    ):
        if not _is_internet_available():
            show_tooltip(
                "🔌 No Internet Connection detected. Please check your internet connection and try again.",
                period=5000,
            )
        else:
            message = (
                "🚧 We’re unable to reach AnkiHub due to planned maintenance or an unexpected issue.<br> "
                "For details, see https://community.ankihub.net/c/announcements."
            )
            show_tooltip(message, period=5000)
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

    if isinstance(exc_value, MissingValueError):
        config.set_download_full_deck_on_next_sync(exc_value.ah_did, True)
        LOGGER.warning(
            "MissingValueError was handled",
            ankihub_did=exc_value.ah_did,
        )
        show_error_dialog(
            "There is an issue with the AnkiHub deck.<br>"
            "Please sync with AnkiHub to resolve it.",
            title="AnkiHub",
        )
        return True

    if isinstance(
        exc_value, OSError
    ) and "Could not find a suitable TLS CA certificate bundle" in str(exc_value):
        showInfo("Please restart Anki.", title="AnkiHub")
        LOGGER.warning("TLS CA certificate bundle error was handled")
        return True

    return False


def _is_internet_available():
    try:
        # Connect to 8.8.8.8 (Google DNS) with a timeout of 3 seconds
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False


def _maybe_handle_ankihub_http_error(error: AnkiHubHTTPError) -> bool:
    """Return True if the error was handled, False otherwise."""
    response = error.response
    if response.status_code == 401:
        config.save_token("")
        from .menu import AnkiHubLogin

        AnkiHubLogin.display_login()
        return True
    elif response.status_code == 403:
        try:
            response_data = response.json()
        except JSONDecodeError:
            return False

        if response_data.get("detail") == TERMS_AGREEMENT_NOT_ACCEPTED_DETAIL:
            run_with_delay_when_progress_dialog_is_open(
                TermsAndConditionsDialog.display, parent=aqt.mw
            )
            return True

    elif response.status_code == 406:
        try:
            response_data = response.json()
        except ValueError:
            return False

        if response_data.get("detail") == OUTDATED_CLIENT_RESPONSE_DETAIL:
            if ask_user(
                "The AnkiHub add-on needs to be updated to continue working.<br>"
                "Do you want to open the add-on update dialog now?",
                parent=aqt.mw,
            ):
                check_and_prompt_for_updates_on_main_window()
            return True
        else:
            return False

    try:
        response_data = response.json()
    except JSONDecodeError:
        return False

    try:
        error_message = response_data.get("detail")
    except:
        return False

    if error_message:
        LOGGER.info("AnkiHubRequestError was handled", error_message=error_message)
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
        before_send=_before_send,
    )


def _before_send(
    event: Dict[str, Any], hint: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Filter out events created by the LoggingIntegration that are not related to this add-on."""
    if "log_record" in hint:
        logger_name = hint["log_record"].name
        if logger_name != LOGGER.name:
            return None
    return event


def _report_exception(
    exception: BaseException, context: Dict[str, Dict[str, Any]] = {}
) -> Optional[str]:
    """Report an exception to Sentry."""
    if not _error_reporting_enabled():
        return None

    with push_scope() as scope:
        scope.level = "error"
        scope.user = {"id": config.username_or_email()}
        scope.set_tag("os", sys.platform)
        scope.set_context("add-on config", dataclasses.asdict(config._private_config))
        scope.set_context("addon version", {"version": ADDON_VERSION})
        scope.set_context("anki version", {"version": ANKI_VERSION})

        try:
            scope.set_context("ankihub base files", _ankihub_base_path_context_dict())
        except Exception as e:
            LOGGER.warning("Could not get ankihub base files context.", exc_info=e)

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


def _ankihub_base_path_context_dict() -> Dict[str, Any]:
    """Return a dict with information about the files of the AnkiHub add-on to be sent as
    context to Sentry."""
    all_file_paths = [
        ankihub_base_path(),
        *list(ankihub_base_path().rglob("*")),
    ]
    problematic_file_paths = []
    if is_win:
        problematic_file_paths = [
            file for file in all_file_paths if not _file_is_accessible(file)
        ]

    result = {
        "all files": [str(file) for file in all_file_paths],
        "inaccessible files (Windows)": [str(file) for file in problematic_file_paths],
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


def _on_upload_logs_failure(exc: Exception) -> None:
    if isinstance(exc, AnkiHubHTTPError):
        # Don't report outdated client errors that happen when uploading logs,
        # because they are handled by the add-on when they happen in other places
        # and we don't want to see them in Sentry.
        if exc.response.status_code == 401 or exc.response.status_code == 406:
            return
        _report_exception(exc)
    else:
        _report_exception(exc)
