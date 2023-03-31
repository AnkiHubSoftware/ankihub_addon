import logging
import os
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, List

import aqt
from anki.hooks import wrap
from aqt import addons
from aqt.addons import AddonManager, DownloaderInstaller

from . import LOGGER
from .db import detach_ankihub_db_from_anki_db_connection
from .settings import log_file_path, setup_logger


def setup_addons():
    _raise_exceptions_on_otherwise_silent_addon_update_failures()

    _prevent_errors_during_addon_updates_and_deletions()

    _prevent_ui_deadlock_of_update_dialog_with_progress_dialog()


def _raise_exceptions_on_otherwise_silent_addon_update_failures():
    # this prevents silent add-on update failures like the ones reported here:
    # https://community.ankihub.net/t/bug-improve-ankihub-addon-update-process/557/5
    # it changes the behavior of _download_done so that it checks if the future has an exception
    DownloaderInstaller._download_done = wrap(  # type: ignore
        old=DownloaderInstaller._download_done,
        new=_check_future_for_exceptions,
        pos="around",
    )


def _check_future_for_exceptions(*args: Any, **kwargs: Any) -> None:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    _old(*args, **kwargs)

    # in future Anki version the argument could be passed differently
    # so we check all arguments for a Future
    future: Future = next((x for x in args if isinstance(x, Future)), None)
    if future is None:
        future = kwargs.get("future", None)

    if future is None:
        raise ValueError("Could not find future argument")

    # throws exception if there was one in the future
    future.result()


def _prevent_errors_during_addon_updates_and_deletions():
    """Prevents errors during add-on updates and deletions on Windows.

    AddonManager calls these methods during an update:
    - backupUserFiles
    - deleteAddon
    - restoreUserFiles
    We need to disable the log file handler while these methods are running because they operate on files
    in the user files directory and there will be permission errors on Windows if we have open file handles
    for files in the user files directory during these operations.

    We also detach the AnkiHub database from the Anki database connection and change the file permissions
    of the files in the user files directory for the same reason.
    """

    # Add _with_disabled_log_file_handler to all methods that operate on files in the user files directory.
    AddonManager.backupUserFiles = wrap(  # type: ignore
        old=AddonManager.backupUserFiles,
        new=_with_disabled_log_file_handler,
        pos="around",
    )

    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=_with_disabled_log_file_handler,
        pos="around",
    )

    AddonManager.restoreUserFiles = wrap(  # type: ignore
        old=AddonManager.restoreUserFiles,
        new=_with_disabled_log_file_handler,
        pos="around",
    )

    # Add _detach_ankihub_db to backupUserFiles and deleteAddon.
    # We don't need to add it to restoreUserFiles because backupUserFiles is always called before restoreUserFiles.
    AddonManager.backupUserFiles = wrap(  # type: ignore
        old=AddonManager.backupUserFiles,
        new=_detach_ankihub_db,
        pos="before",
    )

    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=_detach_ankihub_db,
        pos="before",
    )

    # Add _maybe_change_file_permissions_of_addon_files to backupUserFiles and deleteAddon.
    # We don't need to add it to restoreUserFiles because backupUserFiles is always called before restoreUserFiles.
    AddonManager.backupUserFiles = wrap(  # type: ignore
        old=AddonManager.backupUserFiles,
        new=lambda self, sid: _maybe_change_file_permissions_of_addon_files(sid),
        pos="before",
    )

    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=lambda self, module: _maybe_change_file_permissions_of_addon_files(module),
        pos="before",
    )


def _with_disabled_log_file_handler(*args: Any, **kwargs: Any) -> Any:
    """Disables the log FileHandler while the wrapped method is running.
    Only enables it again if the user files folder still exists after the wrapped method was called.
    """

    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    LOGGER.info(f"Maybe disabling log FileHandler because {_old.__name__} was called.")
    file_handlers = _log_file_handlers()
    assert len(file_handlers) <= 1
    if file_handlers:
        handler = file_handlers[0]
        LOGGER.info(f"Disabling FileHandler: {handler}.")
        handler.close()
        LOGGER.root.removeHandler(handler)

    try:
        result = _old(*args, **kwargs)
    finally:
        assert len(file_handlers) <= 1
        # Only re-enable the log FileHandler if the user files folder still exists and
        # the FileHandler is disabled.
        if log_file_path().parent.exists() and not file_handlers:
            setup_logger()
            LOGGER.info(f"Re-enabled FileHandler after {_old.__name__} was called.")
    return result


def _log_file_handlers() -> List[logging.FileHandler]:
    return [
        handler
        for handler in LOGGER.root.handlers
        if isinstance(handler, logging.FileHandler)
    ]


def _detach_ankihub_db(*args: Any, **kwargs: Any) -> None:
    detach_ankihub_db_from_anki_db_connection()


def _maybe_change_file_permissions_of_addon_files(module: str) -> None:
    ankihub_module = aqt.mw.addonManager.addonFromModule(__name__)
    if module != ankihub_module:
        LOGGER.info(
            f"Did not change file permissions because {module} is not {ankihub_module}"
        )
        return

    addon_dir = Path(aqt.mw.addonManager.addonsFolder(module))
    _change_file_permissions_of_addon_files(addon_dir=addon_dir)


def _change_file_permissions_of_addon_files(addon_dir: Path) -> None:
    for file in addon_dir.rglob("*"):
        if file.is_dir():
            os.chmod(file, 0o777)
        else:
            os.chmod(file, 0o666)
    LOGGER.info(f"On deleteAddon changed file permissions for all files in {addon_dir}")


def _prevent_ui_deadlock_of_update_dialog_with_progress_dialog():
    # prevent the situation that the add-on update dialog is shown while the progress dialog is open which can
    # lead to a deadlock when AnkiHub is syncing and there is an add-on update.
    addons.prompt_to_update = wrap(  # type: ignore
        old=addons.prompt_to_update,
        new=_with_delay_when_progress_dialog_is_open,
        pos="around",
    )


def _with_delay_when_progress_dialog_is_open(*args, **kwargs) -> Any:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    def wrapper():
        LOGGER.info("Calling with_delay_when_progress_dialog_is_open._old")
        _old(*args, **kwargs)

        # the documentation of aqt.mw.progress.timer says that the timer has to be deleted to
        # prevent memory leaks
        timer.deleteLater()

    # aqt.mw.progress.timer is there for creating "Custom timers which avoid firing while a progress dialog is active".
    # It's better to use a large delay value because there is a 0.5 second time window in which
    # the func can be called even if the progress dialog is not closed yet.
    # See https://github.com/ankitects/anki/blob/d9f1e2264804481a2549b23dbc8a530857ad57fc/qt/aqt/progress.py#L261-L277
    timer = aqt.mw.progress.timer(
        ms=2000,
        func=wrapper,
        repeat=False,
        requiresCollection=True,
        parent=aqt.mw,
    )
