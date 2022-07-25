import logging
import logging.config
import os
import pathlib
import sys

from . import settings

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

logging.config.dictConfig(settings.LOGGING)
LOGGER: logging.Logger = logging.getLogger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.debug(f"SKIP_INIT: {SKIP_INIT}")

if not SKIP_INIT:
    try:
        from . import entry_point
        from .error_reporting import report_exception_and_upload_logs

        mw = entry_point.run()
    except Exception as e:
        # should we show exceptions to the user too?
        LOGGER.exception(e)
        report_exception_and_upload_logs()
