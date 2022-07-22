import dataclasses
import logging.config
import os
import pathlib
import sys
from typing import Optional

from . import settings

lib = (pathlib.Path(__file__).parent / "lib").absolute()
sys.path.insert(0, str(lib))

logging.config.dictConfig(settings.LOGGING)
LOGGER = logging.getLogger("ankihub")

SKIP_INIT = os.getenv("SKIP_INIT", False)
LOGGER.debug(f"SKIP_INIT: {SKIP_INIT}")

version_file = pathlib.Path(__file__).parent / "VERSION"
LOGGER.debug(f"VERSION file: {version_file}")
with version_file.open() as f:
    version = f.read().strip()

LOGGER.debug(f"version: {version}")

sentry_env = "anki_desktop"
# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = version
os.environ["SENTRY_ENVIRONMENT"] = sentry_env


def report_exception(context: dict = dict()) -> Optional[str]:
    from .config import config
    from .lib import sentry_sdk  # type: ignore
    from .lib.sentry_sdk import capture_exception, configure_scope  # type: ignore

    if not config.public_config.get("report_errors"):
        return None

    try:
        sentry_sdk.init(
            dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
            traces_sample_rate=1.0,
            release=version,
            environment=sentry_env,
        )
        LOGGER.debug("Sentry initialized.")

        with configure_scope() as scope:
            scope.level = "error"
            scope.user = {"id": config.private_config.user}
            scope.set_context(
                "add-on config", dataclasses.asdict(config.private_config)
            )
            for name, ctx in context.items():
                scope.set_context(name, ctx)

        event_id = capture_exception()
        LOGGER.debug(f"Sentry captured {event_id=}.")
        sentry_sdk.flush()
    except Exception as e:
        LOGGER.debug(f"Reporting to sentry failed: {e}")
        return None
    finally:
        sentry_sdk.init("")
        LOGGER.debug("Sentry disabled.")
    return event_id


if not SKIP_INIT:
    try:
        from . import entry_point

        mw = entry_point.run()
    except Exception as e:
        # should we show exceptions to the user too?
        LOGGER.exception(e)
        report_exception()
