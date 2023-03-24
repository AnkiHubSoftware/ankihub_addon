"""Log or report extra information to sentry in certain situations to help debug issues."""
import os
import traceback
from pathlib import Path
from typing import Any, Callable, Dict

import aqt
from anki.hooks import wrap
from anki.utils import is_win
from aqt.addons import AddonManager

from . import LOGGER
from .db import is_ankihub_db_attached_to_anki_db
from .errors import report_exception_and_upload_logs


def _with_sentry_report_about_user_files_on_error(*args: Any, **kwargs: Any) -> Any:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    try:
        return _old(*args, **kwargs)
    except Exception as e:
        _report_user_files_debug_info_to_sentry(e)
        raise e


def _report_user_files_debug_info_to_sentry(e: Exception) -> None:
    report_exception_and_upload_logs(e, context=_user_files_context_dict())


def _user_files_context_dict() -> Dict[str, Dict[str, Any]]:
    """Return a dict with information about the user files of the AnkiHub add-on."""
    ankihub_module = aqt.mw.addonManager.addonFromModule(__name__)
    user_files_path = Path(aqt.mw.addonManager._userFilesPath(ankihub_module))
    all_file_paths = [user_files_path, *list(user_files_path.rglob("*"))]
    problematic_file_paths = []
    if is_win:
        problematic_file_paths = [
            file for file in all_file_paths if not _file_is_accessible(file)
        ]

    result = {
        "Add-on update debug info": {
            "all files": [str(file) for file in all_file_paths],
            "inaccessible files": [str(file) for file in problematic_file_paths],
            "Is ankihub database attached to anki database?": is_ankihub_db_attached_to_anki_db(),
        },
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


def _log_stack(title: str):
    stack_trace = "\n".join(traceback.format_stack())
    LOGGER.info(f"Stack trace ({title}):\n{stack_trace}")


def _setup_logging_for_sync_collection_and_media():
    # Log stack trace when mw._sync_collection_and_media is called to debug the
    # "Cannot start transaction within transaction" error that occurs when two syncs
    # are started at the same time.
    aqt.mw._sync_collection_and_media = wrap(  # type: ignore
        aqt.mw._sync_collection_and_media,
        lambda *args, **kwargs: _log_stack("mw._sync_collection_and_media"),
        "before",
    )


def _setup_sentry_reporting_for_error_on_addon_update():
    # Sends additional information to Sentry when an add-on update fails.
    # When Anki tries to backup user files before updating an add-on, it fails sometimes with
    # an PermissionError on windows. We are already doing things to prevent this: we are closing the log file handler,
    # changing permissions of files in the folder and detaching the ankihub database from the Anki database.
    # Howevers some users still experience this.
    # Sentry issue: https://ankihub.sentry.io/issues/3942021163/events/d19e160cae10462d8b5b74c797cfd737/?project=6546414
    AddonManager._install = wrap(  # type: ignore
        old=AddonManager._install,
        new=_with_sentry_report_about_user_files_on_error,
        pos="around",
    )


def setup():
    _setup_logging_for_sync_collection_and_media()
    _setup_sentry_reporting_for_error_on_addon_update()
