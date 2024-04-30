"""Error dialog for errors related to the AnkiHub add-on."""
from textwrap import dedent
from traceback import format_exception
from typing import Optional
from urllib.parse import quote

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

from .utils import active_window_or_mw


class ErrorDialog(QDialog):
    """Error dialog for errors related to the AnkiHub add-on."""

    def __init__(
        self, exception: BaseException, sentry_event_id: Optional[str], parent=None
    ):
        super().__init__(parent or active_window_or_mw())
        self.setWindowTitle("AnkiHub add-on error")
        self.setMinimumHeight(400)
        self.setMinimumWidth(500)

        self.layout_ = QVBoxLayout(self)

        message = dedent(
            """
            🐛 Oh no! An AnkiHub add-on error has occurred.

            Click 'Report Error' if you'd like to report this on https://community.ankihub.net/.
            """.strip("\n")  # fmt: skip
        )

        self.message_widget = QLabel(message)
        self.message_widget.setWordWrap(True)
        self.layout_.addWidget(self.message_widget)

        self._setup_debug_info(self.layout_, exception)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.No | QDialogButtonBox.StandardButton.Yes  # type: ignore
        )

        # Changing text of the default yes/no buttons
        yes_button = self.button_box.button(QDialogButtonBox.StandardButton.Yes)  # type: ignore
        yes_button.setText("Report error")

        no_button = self.button_box.button(QDialogButtonBox.StandardButton.No)  # type: ignore
        no_button.setText("Cancel")

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

        self.debug_info_widget = QTextBrowser()
        self.debug_info_widget.setOpenExternalLinks(True)
        self.debug_info_widget.setPlainText(_debug_info(exception))

        self.debug_info_area.setWidget(self.debug_info_widget)
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
            self.debug_info_button.setText("Hide error details")
            self.debug_info_area.show()
            self.spacer_widget.hide()
        else:
            self.debug_info_button.setText("Show error details")
            self.debug_info_area.hide()
            self.spacer_widget.show()


def _debug_info(exception: BaseException) -> str:
    exception_text = "\n".join(
        format_exception(None, value=exception, tb=exception.__traceback__)
    )
    result = f"{utils.supportText()}\n{exception_text}"
    return result


def _forum_url(exception: BaseException, sentry_event_id: Optional[str]):
    """Return a URL to the AnkiHub forum with a new topic pre-filled with the exception
    traceback and a link to the Sentry event (if available)."""
    forum_post_text = (
        dedent(
            """
            **Before the error happened, I was...**
            [Replace this text.]





            <details><summary>Error message (don't change this)</summary>
            """.strip("\n")  # fmt: skip
        )
        + f"\n```\n{_debug_info(exception)}```"
        + "\n</details>"
    )

    if sentry_event_id:
        sentry_url = f"https://ankihub.sentry.io/issues/?project=6546414&query=id:{sentry_event_id}"
        forum_post_text += f"\n\n[Sentry link (for developers)]({sentry_url})"

    result = (
        "https://community.ankihub.net/new-topic?category=bug&tags=add-on-error&body="
        + quote(forum_post_text, safe="")
    )
    return result
