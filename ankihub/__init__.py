import os
import pathlib
import sys

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

lib_other = (pathlib.Path(__file__).parent / "lib/other").absolute()
sys.path.insert(0, str(lib_other))


import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        # Transform event dict into `logging.Logger` method arguments.
        # "event" becomes "msg" and the rest is passed as a dict in
        # "extra". IMPORTANT: This means that the standard library MUST
        # render "extra" for the context to appear in log entries! See
        # warning below.
        structlog.stdlib.render_to_log_kwargs,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
LOGGER = structlog.stdlib.get_logger()

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.info(f"SKIP_INIT: {SKIP_INIT}")


def debug() -> None:
    from aqt.qt import pyqtRemoveInputHook

    pyqtRemoveInputHook()
    breakpoint()


if not SKIP_INIT:
    from . import entry_point

    entry_point.run()
