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
    QScrollArea,
    QSizePolicy,
    Qt,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
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
        🐛 Oh no! An AnkiHub add-on error has occurred.
        Click "Yes," to provide feedback or request help on the AnkiHub forum.

        The AnkiHub team will respond ASAP!
        """.strip("\n")  # fmt: skip
    )
    message_widget = QLabel(message)
    message_widget.setWordWrap(True)
    layout.addWidget(message_widget)

    setup_debug_info(layout, exception)

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


def setup_debug_info(layout: QVBoxLayout, exception: BaseException) -> None:
    """Setup the debug info text browser which contains the exception traceback.
    The debug info is hidden by default, but can be toggled by clicking a button."""
    debug_info_button = layout.debug_info_toggle_button = QToolButton()  # type: ignore
    layout.addWidget(debug_info_button)

    debug_info_area = layout.scroll_area = QScrollArea()  # type: ignore
    layout.addWidget(layout.scroll_area)  # type: ignore

    exception_widget = QTextBrowser()
    exception_widget.setOpenExternalLinks(True)
    exception_text = "".join(
        format_exception(None, value=exception, tb=exception.__traceback__)
    )
    exception_widget.setPlainText(exception_text)

    debug_info_area.setWidget(exception_widget)
    debug_info_area.setWidgetResizable(True)
    debug_info_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # ... the spacer widget keeps the layout intact when the debug info is hidden.
    spacer_widget = layout.spacer_widget = QWidget()  # type: ignore
    spacer_widget.setSizePolicy(
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
    )
    layout.addWidget(spacer_widget)

    def toggle_debug_info() -> None:
        if debug_info_area.isHidden():
            debug_info_button.setText("Hide Debug Info")
            debug_info_area.show()
            spacer_widget.hide()
        else:
            debug_info_button.setText("Show Debug Info")
            debug_info_area.hide()
            spacer_widget.show()

    qconnect(debug_info_button.clicked, toggle_debug_info)

    # ... hide debug info by default
    toggle_debug_info()


def _forum_url(exception: BaseException, sentry_event_id: Optional[str]):
    exception_html = "<br>".join(
        format_exception(None, value=exception, tb=exception.__traceback__)
    )
    forum_post_text = (
        dedent(
            """
            **Before the error happened, I was...**
            [Replace this text.]





            <details><summary>Error message (don't change this)</summary>
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
