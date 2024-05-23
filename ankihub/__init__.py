import os
import pathlib
import sys

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

lib_other = (pathlib.Path(__file__).parent / "lib/other").absolute()
sys.path.insert(0, str(lib_other))


import structlog

shared_processors = [
    structlog.stdlib.filter_by_level,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]

structlog.configure(
    processors=shared_processors
    + [
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

LOGGER = structlog.stdlib.get_logger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.info(f"SKIP_INIT: {SKIP_INIT}")


def debug() -> None:
    from aqt.qt import pyqtRemoveInputHook

    pyqtRemoveInputHook()
    breakpoint()


if not SKIP_INIT:
    from . import entry_point

    entry_point.run()
