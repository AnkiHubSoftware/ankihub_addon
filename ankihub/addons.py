import logging
import os
from pathlib import Path
from typing import Any, Callable

from anki.hooks import wrap
from aqt import addons, mw
from aqt.addons import AddonManager, DownloaderInstaller

from . import LOGGER
from .db import detach_ankihub_db_from_anki_db_connection
from .settings import log_file_path, file_handler


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


def on_deleteAddon(self, module: str) -> None:
    # without this Anki is not able to delete all contents of the media_import libs folder
    # on Windows
    ankihub_module = mw.addonManager.addonFromModule(__name__)
    if module != ankihub_module:
        return

    addon_dir = Path(self.addonsFolder(module))
    for file in addon_dir.rglob("*"):
        os.chmod(file, 0o777)
    LOGGER.info(f"On deleteAddon changed file permissions for all files in {addon_dir}")


def with_hidden_progress_dialog(*args, **kwargs) -> Any:
    # When Anki checks for add-on updates and AnkiHub syncs at the same time there can be a UI "deadlock",
    # because the progress dialog is blocking the ChooseAddonsToUpdateDialog (not really, see below)
    # and the closure that shows the ChooseAddonsToUpdateDialog blocks the closure for closing the progress dialog
    # in mw.taskman.
    # It's not really a deadlock, because you can interact with the ChooseAddonsToUpdateDialog despite
    # the busy mouse cursor, but it looks like one.

    LOGGER.info("From with_hidden_progress_dialog")

    did_hide_dialog = False
    if mw.progress._win:
        LOGGER.info("Hiding progress dialog")
        mw.progress._win.hide()
        mw.progress._restore_cursor()
        did_hide_dialog = True

    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    result = _old(*args, **kwargs)

    if mw.progress._win and did_hide_dialog:
        LOGGER.info("Restoring progress dialog")
        mw.progress._win.show()
        mw.progress._set_busy_cursor()

    return result


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

    # prevent errors when deleting the add-on (AddonManager.deleteAddon also gets called during an update)
    AddonManager.deleteAddon = wrap(  # type: ignore
        old=AddonManager.deleteAddon,
        new=on_deleteAddon,
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

    # prevent UI "deadlock" when Anki checks for add-on updates and AnkiHub syncs at the same time
    addons.prompt_to_update = wrap(  # type: ignore
        old=addons.prompt_to_update, new=with_hidden_progress_dialog, pos="around"
    )
