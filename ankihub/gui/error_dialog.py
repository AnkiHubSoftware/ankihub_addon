"""Error dialog for errors related to the AnkiHub add-on."""
from textwrap import dedent
from traceback import format_exception
from typing import Optional
from urllib.parse import quote

import aqt
from aqt import utils
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


class ErrorDialog(QDialog):
    """Error dialog for errors related to the AnkiHub add-on."""

    def __init__(
        self, exception: BaseException, sentry_event_id: Optional[str], parent=None
    ):
        super().__init__(parent or aqt.mw.app.activeWindow() or aqt.mw)
        self.setWindowTitle("AnkiHub add-on error")
        self.setMinimumHeight(400)
        self.setMinimumWidth(500)

        self.layout_ = QVBoxLayout(self)

        message = dedent(
            """
            🐛 Oh no! An AnkiHub add-on error has occurred.
            Click "Yes," to provide feedback or request help on the AnkiHub forum.

            The AnkiHub team will respond ASAP!
            """.strip("\n")  # fmt: skip
        )

        self.message_widget = QLabel(message)
        self.message_widget.setWordWrap(True)
        self.layout_.addWidget(self.message_widget)

        self._setup_debug_info(self.layout_, exception)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.No | QDialogButtonBox.StandardButton.Yes  # type: ignore
        )

        def on_accepted() -> None:
            utils.openLink(_forum_url(exception, sentry_event_id))
            self.accept()

        qconnect(self.button_box.accepted, on_accepted)

        def on_rejected() -> None:
            self.reject()

        qconnect(self.button_box.rejected, on_rejected)

        self.layout_.addWidget(self.button_box)

    def _setup_debug_info(self, layout: QVBoxLayout, exception: BaseException) -> None:
        """Setup the debug info text browser which contains the exception traceback.
        The debug info is hidden by default, but can be toggled by clicking a button."""
        self.debug_info_button = QToolButton()  # type: ignore
        qconnect(self.debug_info_button.clicked, self._toggle_debug_info)
        layout.addWidget(self.debug_info_button)

        self.debug_info_area = QScrollArea()  # type: ignore
        layout.addWidget(self.debug_info_area)  # type: ignore

        self.exception_widget = QTextBrowser()
        self.exception_widget.setOpenExternalLinks(True)
        exception_text = "".join(
            format_exception(None, value=exception, tb=exception.__traceback__)
        )
        self.exception_widget.setPlainText(exception_text)

        self.debug_info_area.setWidget(self.exception_widget)
        self.debug_info_area.setWidgetResizable(True)
        self.debug_info_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.spacer_widget = layout.spacer_widget = QWidget()  # type: ignore
        self.spacer_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.spacer_widget)

        # Hide debug info by default
        self._toggle_debug_info()

    def _toggle_debug_info(self) -> None:
        if self.debug_info_area.isHidden():
            self.debug_info_button.setText("Hide Debug Info")
            self.debug_info_area.show()
            self.spacer_widget.hide()
        else:
            self.debug_info_button.setText("Show Debug Info")
            self.debug_info_area.hide()
            self.spacer_widget.show()


def _forum_url(exception: BaseException, sentry_event_id: Optional[str]):
    """Return a URL to the AnkiHub forum with a new topic pre-filled with the exception
    traceback and a link to the Sentry event (if available)."""
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
