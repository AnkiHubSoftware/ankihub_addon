import os
import re
import sys
import traceback

from aqt.gui_hooks import main_window_did_init

from .config import config
from .constants import ANKIWEB_ID
from .error_reporting import report_exception_and_upload_logs
from .gui.error_feedback import ErrorFeedbackDialog


def overwrite_excepthook():
    def excepthook(etype, val: Exception, tb) -> None:
        try:
            tb_str = "".join(traceback.format_tb(tb))
            this_addon_is_involved = (
                ANKIWEB_ID is not None
                and re.search(rf"/addons21/(ankihub|{ANKIWEB_ID})/", tb_str)
            ) or (ANKIWEB_ID is None and re.search(r"/addons21/ankihub", tb_str))

            if (
                this_addon_is_involved
                and config.public_config.get("report_errors")
                and not os.getenv("REPORT_ERRORS", None) == "0"
            ):
                sentry_id = report_exception_and_upload_logs(exception=val)
                ErrorFeedbackDialog(exception=val, event_id=sentry_id)
        finally:
            original_except_hook(etype, val, tb)

    original_except_hook = sys.excepthook
    sys.excepthook = excepthook


def setup_error_handler():
    main_window_did_init.append(overwrite_excepthook)
