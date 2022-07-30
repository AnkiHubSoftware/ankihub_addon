import dataclasses
import os
import time
from concurrent.futures import Future
from typing import Optional

from aqt import mw

from ankihub.ankihub_client import AnkiHubRequestError

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config
from .constants import ANKI_VERSION, ADDON_VERSION
from .settings import LOG_FILE

SENTRY_ENV = "anki_desktop"
# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = ADDON_VERSION
os.environ["SENTRY_ENVIRONMENT"] = SENTRY_ENV


def report_exception_and_upload_logs(
    exception: Optional[BaseException] = None, context: dict = dict()
) -> Optional[str]:
    if not config.public_config.get("report_errors"):
        return None

    if os.getenv("REPORT_ERRORS", None) == "0":
        return None

    logs_key = upload_logs_in_background()
    context = {**context, "logs": {"filename": logs_key}}
    sentry_event_id = report_exception(exception=exception, context=context)
    return sentry_event_id


def report_exception(
    exception: Optional[BaseException] = None, context: dict = dict()
) -> Optional[str]:
    try:
        from .config import config
        from .lib import sentry_sdk  # type: ignore
        from .lib.sentry_sdk import capture_exception, configure_scope  # type: ignore

        sentry_sdk.init(
            dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
            traces_sample_rate=1.0,
            release=ADDON_VERSION,
            environment=SENTRY_ENV,
        )
        LOGGER.debug("Sentry initialized.")

        with configure_scope() as scope:
            scope.level = "error"
            scope.user = {"id": config.private_config.user}
            scope.set_context(
                "add-on config", dataclasses.asdict(config.private_config)
            )
            scope.set_context("addon version", {"version": ADDON_VERSION})
            scope.set_context("anki version", {"version": ANKI_VERSION})
            for name, ctx in context.items():
                scope.set_context(name, ctx)

        if exception is None:
            event_id = capture_exception()
        else:
            event_id = capture_exception(exception)
        LOGGER.debug(f"Sentry captured {event_id=}.")
        sentry_sdk.flush()
    except Exception as e:
        LOGGER.debug(f"Reporting to sentry failed: {e}")
        return None
    finally:
        sentry_sdk.init("")
        LOGGER.debug("Sentry disabled.")
    return event_id


def upload_logs_in_background() -> str:
    LOGGER.debug("Uploading logs...")
    key = f"ankihub_addon_logs_{config.private_config.user}_{int(time.time())}.log"

    def upload_logs():
        try:
            client = AnkiHubClient()
            client.upload_logs(
                file=LOG_FILE,
                key=key,
            )
            LOGGER.debug("Logs uploaded.")
        except AnkiHubRequestError:
            LOGGER.debug("Logs upload failed.")

    def on_upload_logs_done(future: Future) -> None:
        try:
            future.result()
        except Exception:
            report_exception()

    mw.taskman.run_in_background(task=upload_logs, on_done=on_upload_logs_done)

    return key
