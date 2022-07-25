import logging
from typing import Any, Callable

from anki.hooks import wrap
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


def setup_addons():
    # This is done to prevent "Cannot access file being used by another process" errors
    # when the add-on is writing to a log file while Anki is updating or deleting the add-on.

    AddonManager.deleteAddon = wrap(
        old=AddonManager.deleteAddon, new=without_logging, pos="around"
    )

    DownloaderInstaller.download = wrap(
        old=DownloaderInstaller.download, new=without_logging, pos="around"
    )
