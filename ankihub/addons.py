import logging
import os
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

from anki.hooks import wrap
from aqt import addons, mw
from aqt.addons import AddonManager, DownloaderInstaller

from . import LOGGER
from .db import detach_ankihub_db_from_anki_db_connection
from .settings import file_handler, log_file_path


def with_disabled_log_file_handler(*args: Any, **kwargs: Any) -> Any:
    # This is done to prevent "Cannot access file being used by another process" errors
    # when Anki operates on files of the add-on

    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    LOGGER.info(f"Disabling log FileHandlers because {_old.__name__} was called.")
    handlers = LOGGER.root.handlers[:]
    for handler in handlers:
        if isinstance(handler, logging.FileHandler):
            LOGGER.info(
                f"Removing handler: {handler}",
            )
            LOGGER.root.removeHandler(handler)
            handler.close()

    result = _old(*args, **kwargs)

    # if the add-on was deleted it makes no sense to re-add the FileHandler (and it throws an error)
    if log_file_path().parent.exists():
        LOGGER.root.addHandler(file_handler())
        LOGGER.info("Re-added FileHandler")

    return result


def detach_ankihub_db(*args: Any, **kwargs: Any) -> None:
    detach_ankihub_db_from_anki_db_connection()


def maybe_change_file_permissions_of_addon_files(module: str) -> None:
    ankihub_module = mw.addonManager.addonFromModule(__name__)
    if module != ankihub_module:
        LOGGER.info(
            f"Did not change file permissions because {module} is not {ankihub_module}"
        )
        return

    addon_dir = Path(mw.addonManager.addonsFolder(ankihub_module))
    for file in addon_dir.rglob("*"):
        if file.is_dir():
            os.chmod(file, 0o777)
        else:
            os.chmod(file, 0o666)
    LOGGER.info(f"On deleteAddon changed file permissions for all files in {addon_dir}")


def check_future_for_exceptions(*args: Any, **kwargs: Any) -> None:
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


def setup_addons():

    # prevent errors when updating the add-on
    DownloaderInstaller._download_all = wrap(  # type: ignore
        old=DownloaderInstaller._download_all,
        new=with_disabled_log_file_handler,
        pos="around",
    )

    DownloaderInstaller._download_all = wrap(  # type: ignore
        old=DownloaderInstaller._download_all,
        new=detach_ankihub_db,
        pos="before",
    )

    AddonManager._install = wrap(  # type: ignore
        old=AddonManager._install,
        new=lambda self, module, zfile: maybe_change_file_permissions_of_addon_files(
            module
        ),
        pos="before",
    )

    # prevent errors when deleting the add-on (AddonManager.deleteAddon also gets called during an update)
    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=lambda self, module: maybe_change_file_permissions_of_addon_files(module),
        pos="before",
    )

    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=with_disabled_log_file_handler,
        pos="around",
    )

    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=detach_ankihub_db,
        pos="before",
    )

    # prevent the situation that the add-on update dialog is shown while the progress dialog is open which can
    # lead to a deadlock when AnkiHub is syncing and there is an add-on update.
    addons.prompt_to_update = wrap(  # type: ignore
        old=addons.prompt_to_update,
        new=with_delay_when_progress_dialog_is_open,
        pos="around",
    )

    # this prevents silent add-on update failures like the ones reported here:
    # https://community.ankihub.net/t/bug-improve-ankihub-addon-update-process/557/5
    # it changes the behavior of _download_done so that it checks if the future has an exception
    DownloaderInstaller._download_done = wrap(  # type: ignore
        old=DownloaderInstaller._download_done,
        new=check_future_for_exceptions,
        pos="around",
    )


def with_delay_when_progress_dialog_is_open(*args, **kwargs) -> Any:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    def wrapper():
        LOGGER.info("Calling with_delay_when_progress_dialog_is_open._old")
        _old(*args, **kwargs)

        # the documentation of mw.progress.timer says that the timer has to be deleted to
        # prevent memory leaks
        timer.deleteLater()

    # mw.progress.timer is there for creating "Custom timers which avoid firing while a progress dialog is active".
    # It's better to use a large delay value because there is a 0.5 second time window in which
    # the func can be called even if the progress dialog is not closed yet.
    # See https://github.com/ankitects/anki/blob/d9f1e2264804481a2549b23dbc8a530857ad57fc/qt/aqt/progress.py#L261-L277
    timer = mw.progress.timer(
        ms=2000,
        func=wrapper,
        repeat=False,
        requiresCollection=True,
        parent=mw,
    )
