import dataclasses
import os
import time
from concurrent.futures import Future
from typing import Optional

from aqt import mw

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .config import config
from .constants import VERSION, ANKI_VERSION
from .settings import LOG_FILE

SENTRY_ENV = "anki_desktop"
# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = VERSION
os.environ["SENTRY_ENVIRONMENT"] = SENTRY_ENV


def report_exception_and_upload_logs(context: dict = dict()) -> Optional[str]:
    if not config.public_config.get("report_errors"):
        return None

    logs_key = upload_logs_in_background()
    context = {**context, "logs": {"filename": logs_key}}
    sentry_event_id = report_exception(context)
    return sentry_event_id


def report_exception(context: dict = dict()) -> Optional[str]:
    from .config import config
    from .lib import sentry_sdk  # type: ignore
    from .lib.sentry_sdk import capture_exception, configure_scope  # type: ignore

    try:
        sentry_sdk.init(
            dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
            traces_sample_rate=1.0,
            release=VERSION,
            environment=SENTRY_ENV,
        )
        LOGGER.debug("Sentry initialized.")

        with configure_scope() as scope:
            scope.level = "error"
            scope.user = {"id": config.private_config.user}
            scope.set_context(
                "add-on config", dataclasses.asdict(config.private_config)
            )
            scope.set_context("anki version", {"version": ANKI_VERSION})
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


def upload_logs_in_background() -> str:
    LOGGER.debug("Uploading logs...")
    key = f"ankihub_addon_logs_{config.private_config.user}_{int(time.time())}.log"

    def upload_logs():
        client = AnkiHubClient()
        response = client.upload_logs(
            file=LOG_FILE,
            key=key,
        )
        if response.status_code == 200:
            LOGGER.debug("Logs uploaded.")
        else:
            LOGGER.debug("Failed to upload logs.")

    def on_upload_logs_done(future: Future) -> None:
        try:
            future.result()
        except Exception:
            report_exception()

    mw.taskman.run_in_background(task=upload_logs, on_done=on_upload_logs_done)

    return key
