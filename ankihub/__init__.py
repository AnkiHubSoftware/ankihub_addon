import dataclasses
import logging.config
import os
import pathlib
import sys

from . import settings

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

logging.config.dictConfig(settings.LOGGING)
LOGGER = logging.getLogger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.debug(f"SKIP_INIT: {SKIP_INIT}")

version_file = pathlib.Path(__file__).parent / "VERSION"
with version_file.open() as f:
    version = f.read().strip()


def report_exception():
    from .config import config
    from .lib import sentry_sdk  # type: ignore
    from .lib.sentry_sdk import capture_exception, configure_scope  # type: ignore

    if not config.public_config.get("report_errors"):
        return

    sentry_sdk.init(
        dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
        traces_sample_rate=1.0,
        release=version,
    )

    with configure_scope() as scope:
        scope.level = "error"
        scope.user = {"id": config.private_config.user}
        scope.set_context("add-on config", dataclasses.asdict(config.private_config))

    capture_exception()
    sentry_sdk.flush()
    sentry_sdk.init("")


if not SKIP_INIT:
    try:
        from . import entry_point

        mw = entry_point.run()
    except:
        report_exception()
