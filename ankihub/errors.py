import os
import re
import sys
import traceback

from aqt.gui_hooks import main_window_did_init

from .config import config
from .error_reporting import report_exception_and_upload_logs
from .gui.error_feedback import ErrorFeedbackDialog


def overwrite_excepthook():
    def excepthook(etype, val: Exception, tb) -> None:
        tb_str = "".join(traceback.format_tb(tb))
        if (
            not re.search(r"/addons21/ankihub/", tb_str)
            or not config.public_config.get("report_errors")
            or os.getenv("REPORT_ERRORS", None) == "0"
        ):
            sentry_id = report_exception_and_upload_logs(exception=val)
            ErrorFeedbackDialog(sentry_id)
            original_except_hook(etype, val, tb)

        original_except_hook(etype, val, tb)

    original_except_hook = sys.excepthook
    sys.excepthook = excepthook


def setup_error_handler():
    main_window_did_init.append(overwrite_excepthook)
