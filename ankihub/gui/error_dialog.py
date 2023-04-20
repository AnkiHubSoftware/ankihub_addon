"""Error dialog for errors related to the AnkiHub add-on."""
from textwrap import dedent
from traceback import format_exception
from typing import Optional
from urllib.parse import quote

import aqt
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import disable_help_button, openLink


def show_error_dialog(exception: BaseException, sentry_event_id: Optional[str]) -> None:
    """Show an error dialog similar to Anki's error dialog, but it states that the error
    is related to the AnkiHub add-on and asks the user if they want to provide feedback
    about the error on the AnkiHub forum. If the user clicks "Yes", the AnkiHub forum
    will be opened in the user's browser with a pre-filled post."""
    parent = aqt.mw.app.activeWindow() or aqt.mw
    diag = QDialog(parent)
    diag.setWindowTitle("AnkiHub add-on error")
    disable_help_button(diag)
    layout = QVBoxLayout(diag)
    diag.setLayout(layout)

    message = dedent(
        """
        An error related to the AnkiHub add-on has occurred.
        Do you want to provide feedback about this error on the AnkiHub forum?

        When you click "Yes", the AnkiHub forum will be opened in your browser
        with a pre-filled post.

        Debug info:
        """.strip("\n")  # fmt: skip
    )
    message_widget = QLabel(message)
    message_widget.setWordWrap(True)
    layout.addWidget(message_widget)

    exception_text = "".join(
        format_exception(None, value=exception, tb=exception.__traceback__)
    )
    exception_widget = QTextBrowser()
    exception_widget.setOpenExternalLinks(True)
    exception_widget.setPlainText(exception_text)
    layout.addWidget(exception_widget)

    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.No | QDialogButtonBox.StandardButton.Yes  # type: ignore
    )

    def on_accepted() -> None:
        openLink(_forum_url(exception, sentry_event_id))
        diag.accept()

    def on_rejected() -> None:
        diag.reject()

    qconnect(box.accepted, on_accepted)
    qconnect(box.rejected, on_rejected)

    layout.addWidget(box)

    diag.setMinimumHeight(400)
    diag.setMinimumWidth(500)
    diag.exec()


def _forum_url(exception: BaseException, sentry_event_id: Optional[str]):
    exception_html = "<br>".join(
        format_exception(None, value=exception, tb=exception.__traceback__)
    )
    forum_post_text = (
        dedent(
            """
            **What did you do when the error happended?**
            Replace this text.





            **Error information (don't change this)**
            <details><summary>Error message</summary>
            """.strip("\n")  # fmt: skip
        )
        + exception_html
        + "\n</details>"
    )

    if sentry_event_id:
        sentry_url = f"https://ankihub.sentry.io/issues/?project=6546414&query=id:{sentry_event_id}"
        forum_post_text += f"\n\n[Sentry link (for developers)]({sentry_url})"

    result = f"https://community.ankihub.net/new-topic?category=support&tags=bug&body={quote(forum_post_text, safe='')}"
    return result
