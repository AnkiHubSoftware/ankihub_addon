import logging
import os
from pathlib import Path
from typing import Any, Callable

from anki.hooks import wrap
from aqt import addons, mw
from aqt.addons import AddonManager, DownloaderInstaller

from . import LOGGER
from .constants import ANKIWEB_ID
from .settings import LOG_FILE, file_handler


def with_disabled_log_file_handler(*args: Any, **kwargs: Any) -> Any:
    # This is done to prevent "Cannot access file being used by another process" errors
    # when Anki operates on files of the add-on

    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    LOGGER.debug(f"Disabling log FileHandlers because {_old.__name__} was called.")
    handlers = LOGGER.root.handlers[:]
    for handler in handlers:
        if isinstance(handler, logging.FileHandler):
            LOGGER.debug(
                f"Removing handler: {handler}",
            )
            LOGGER.removeHandler(handler)
            LOGGER.root.removeHandler(handler)
            handler.close()
            LOG_FILE.unlink()  # Without this there are PermissionErrors or FileNotFoundErrors on Windows

    result = _old(*args, **kwargs)

    # if the add-on was deleted it makes no sense to re-add the FileHandler and it throws an error
    if LOG_FILE.parent.exists():
        LOGGER.addHandler(file_handler())
        LOGGER.debug("Re-added FileHandler")

    return result


def on_deleteAddon(self, module: str):
    # without this Anki is not able to delete all contents of the media_import libs folder
    # on Windows
    LOGGER.debug(f"on_deleteAddon was called with {module=}")

    if module.lower() not in ["ankihub", str(ANKIWEB_ID)]:
        LOGGER.debug(f"Skipping because {module} is not this add-on.")
        return

    addon_dir = Path(self.addonsFolder(module))
    for file in addon_dir.glob("*"):
        os.chmod(file, 0o777)
    LOGGER.debug(f"Changed file permissions for all files in {addon_dir}")


def before_prompt_to_update(*args, **kwargs):
    # When Anki checks for add-on updates and AnkiHub syncs at the same time there can be a UI deadlock,
    # because the progress dialog is blocking the ChooseAddonsToUpdateDialog
    # and the closure that shows the ChooseAddonsToUpdateDialog blocks the closure for closing the progress dialog
    # in mw.taskman.

    LOGGER.debug("From on_prompt_to_update")
    while mw.progress._levels > 0:
        LOGGER.debug(
            "Calling mw.progress.finish() to prevent the progress dialog blocking the ChooseAddonsToUpdateDialog."
        )
        mw.progress.finish()


def setup_addons():

    # prevent errors when updating the add-on
    DownloaderInstaller._download_all = wrap(
        old=DownloaderInstaller._download_all,
        new=with_disabled_log_file_handler,
        pos="around",
    )

    # prevent errors when deleting the add-on (AddonManager.deleteAddon also gets called during an update)
    AddonManager.deleteAddon = wrap(
        old=AddonManager.deleteAddon,
        new=on_deleteAddon,
        pos="before",
    )
    AddonManager.deleteAddon = wrap(
        old=AddonManager.deleteAddon,
        new=with_disabled_log_file_handler,
        pos="around",
    )

    # prevent UI deadlock when Anki checks for add-on updates and AnkiHub syncs at the same time
    addons.prompt_to_update = wrap(
        old=addons.prompt_to_update, new=before_prompt_to_update, pos="before"
    )
