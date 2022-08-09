import logging
from typing import Any, Callable

from anki.hooks import wrap
from aqt import addons, mw
from aqt.addons import AddonManager, DownloaderInstaller

from . import LOGGER


def without_logging(*args: Any, **kwargs: Any) -> Any:
    _old: Callable = kwargs["_old"]
    del kwargs["_old"]

    LOGGER.debug(f"Disabling logger because {_old.__name__} was called.")
    level_backup = LOGGER.level
    LOGGER.setLevel(logging.CRITICAL)

    result = _old(*args, **kwargs)

    LOGGER.setLevel(level_backup)
    LOGGER.debug("Re-enabling logger.")
    return result


def on_prompt_to_update(*args, **kwargs):
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
    AddonManager.deleteAddon = wrap(
        old=AddonManager.deleteAddon,
        new=with_disabled_logging_file_handler,
        pos="around",
    )

    DownloaderInstaller.download = wrap(
        old=DownloaderInstaller.download,
        new=with_disabled_logging_file_handler,
        pos="around",
    )

    addons.prompt_to_update = wrap(
        old=addons.prompt_to_update, new=on_prompt_to_update, pos="before"
    )
