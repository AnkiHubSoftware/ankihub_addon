import logging
import logging.config
import os
import pathlib
import sys

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

from . import settings  # noqa: E402

LOGGER: logging.Logger = logging.getLogger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.debug(f"SKIP_INIT: {SKIP_INIT}")

LOGGER.debug(f"VERSION file: {settings.version_file}")
LOGGER.debug(f"version: {settings.ADDON_VERSION}")
LOGGER.debug(f"Starting with URL_BASE {settings.API_URL_BASE}")


def debug() -> None:
    from aqt.qt import pyqtRemoveInputHook

    pyqtRemoveInputHook()
    breakpoint()


if not SKIP_INIT:
    from .error_reporting import report_exception_and_upload_logs

    try:
        from . import entry_point

        mw = entry_point.run()
    except Exception as e:
        # should we show exceptions to the user too?
        LOGGER.exception(e)
        report_exception_and_upload_logs()
