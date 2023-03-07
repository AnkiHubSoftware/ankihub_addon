import dataclasses
import os
import re
import time
from concurrent.futures import Future
from typing import Callable, Optional

import aqt
from anki.utils import checksum

from . import LOGGER
from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import AnkiHubRequestError
from .settings import ADDON_VERSION, ANKI_VERSION, config, log_file_path

SENTRY_ENV = "anki_desktop"
# This prevents Sentry from trying to run a git command to infer the version.
os.environ["SENTRY_RELEASE"] = ADDON_VERSION
os.environ["SENTRY_ENVIRONMENT"] = SENTRY_ENV


def report_exception_and_upload_logs(
    exception: Optional[BaseException] = None, context: Optional[dict] = None
) -> Optional[str]:
    if not config.public_config.get("report_errors"):
        return None

    if os.getenv("REPORT_ERRORS", None) == "0":
        return None

    if context is None:
        context = dict()

    logs_key = upload_logs_in_background()
    context = {**context, "logs": {"filename": logs_key}}
    sentry_event_id = report_exception(exception=exception, context=context)
    return sentry_event_id


def report_exception(
    exception: Optional[BaseException] = None, context: dict = dict()
) -> Optional[str]:
    try:
        import sentry_sdk
        from sentry_sdk import capture_exception, configure_scope

        from .settings import config

        sentry_sdk.init(
            dsn="https://715325d30fa44ecd939d12edda720f91@o1184291.ingest.sentry.io/6546414",
            traces_sample_rate=1.0,
            release=ADDON_VERSION,
            environment=SENTRY_ENV,
        )
        LOGGER.info("Sentry initialized.")

        with configure_scope() as scope:
            scope.level = "error"
            scope.user = {"id": config.user()}
            scope.set_context(
                "add-on config", dataclasses.asdict(config._private_config)
            )
            scope.set_context("addon version", {"version": ADDON_VERSION})
            scope.set_context("anki version", {"version": ANKI_VERSION})
            for name, ctx in context.items():
                scope.set_context(name, ctx)

            if isinstance(exception, AnkiHubRequestError):
                scope.set_context(
                    "response",
                    {
                        "url": exception.response.url,
                        "reason": exception.response.reason,
                        "content": exception.response.content,
                    },
                )
                scope.fingerprint = [
                    "{{ default }}",
                    normalize_url(exception.response.url),
                ]

                scope.set_context(
                    "request",
                    {
                        "method": exception.response.request.method,
                        "url": exception.response.request.url,
                        "body": exception.response.request.body,
                    },
                )

        if exception is None:
            event_id = capture_exception()
        else:
            event_id = capture_exception(exception)
        LOGGER.info(f"Sentry captured {event_id=}.")
        sentry_sdk.flush()
    except Exception as e:
        LOGGER.info(f"Reporting to sentry failed: {e}")
        return None
    finally:
        sentry_sdk.init("")
        LOGGER.info("Sentry disabled.")
    return event_id


def normalize_url(url: str):

    # remove parameters
    result = re.sub(r"\?.+$", "", url)

    # replace ids with placeholder
    uuid_re = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    result = re.sub(rf"/({uuid_re}|[0-9]+)", "/<id>", result)
    return result


def upload_logs_in_background(
    on_done: Optional[Callable[[Future], None]] = None, hide_username=False
) -> str:
    LOGGER.info("Uploading logs...")

    # many users use their email address as their username and may not want to share it on a forum
    user_name = config.user() if not hide_username else checksum(config.user())[:5]
    key = f"ankihub_addon_logs_{user_name}_{int(time.time())}.log"

    def upload_logs() -> str:
        try:
            client = AnkiHubClient()
            client.upload_logs(
                file=log_file_path(),
                key=key,
            )
            LOGGER.info("Logs uploaded.")
            return key
        except AnkiHubRequestError as e:
            LOGGER.info("Logs upload failed.")
            raise e

    if on_done is not None:
        aqt.mw.taskman.run_in_background(task=upload_logs, on_done=on_done)
    else:

        def on_upload_logs_done(future: Future) -> None:
            try:
                future.result()
            except Exception:
                report_exception()

        aqt.mw.taskman.run_in_background(task=upload_logs, on_done=on_upload_logs_done)

    return key
