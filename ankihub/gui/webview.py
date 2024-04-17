from abc import abstractmethod
from typing import Any, Optional, cast

from aqt.gui_hooks import theme_did_change
from aqt.qt import (
    QColor,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QUrl,
    QVBoxLayout,
    QWebEngineUrlRequestInterceptor,
    qconnect,
)
from aqt.utils import openLink
from aqt.webview import AnkiWebView

from .. import LOGGER
from ..settings import config


class AnkiHubWebViewDialog(QDialog):
    """A dialog that displays a web view. The purpose is to show an AnkiHub web app page.
    This class handles setting up the web view, loading the page, styling and authentication.
    """

    dialog: Optional["AnkiHubWebViewDialog"] = None

    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self._setup_ui()

    @classmethod
    def display(cls, parent: Any) -> "Optional[AnkiHubWebViewDialog]":
        """Display the dialog. If the dialog is already open, the existing dialog is activated and shown."""
        if not config.token():
            cls._handle_auth_failure()
            return None

        if cls.dialog is None:
            cls.dialog = cls(parent)
        else:
            cls.dialog = cast(AnkiHubWebViewDialog, cls.dialog)

        cls.dialog._load_page()
        cls.dialog.activateWindow()
        cls.dialog.raise_()
        cls.dialog.show()

        return cls.dialog

    def _setup_ui(self) -> None:
        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)

        self.interceptor = AuthenticationRequestInterceptor()
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)
        self.web.page().setBackgroundColor(QColor("white"))

        # Set the background color of the web view back to white when Anki's theme changes it to dark
        theme_did_change.append(
            lambda: self.web.page().setBackgroundColor(QColor("white"))
        )

        self.view_in_web_browser_button = QPushButton("View in web browser")
        self.view_in_web_browser_button.setAutoDefault(False)
        qconnect(
            self.view_in_web_browser_button.clicked,
            lambda: openLink(self._get_non_embed_url()),
        )

        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        qconnect(self.close_button.clicked, self.close)

        self.button_layout = QHBoxLayout()
        self.button_layout.setContentsMargins(10, 5, 10, 10)
        self.button_layout.addSpacing(20)
        self.button_layout.addWidget(self.view_in_web_browser_button)
        self.button_layout.addStretch()
        self.button_layout.addWidget(self.close_button)
        self.button_layout.addSpacing(20)

        self.layout_ = QVBoxLayout()
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.web)
        self.layout_.addLayout(self.button_layout)

        self.setLayout(self.layout_)

        # Set the background color of the dialog and buttons to fit the light theme of the web app.
        # We will always show the light theme version of the embed, so the dialog needs to match.
        self.setStyleSheet(
            self.styleSheet()
            + """
            QDialog {
                background-color: white;
            }
            QPushButton {
                color: black;
                background-color: #fcfcfc;
                border-color: #ccc;
            }
            """
        )

    @abstractmethod
    def _get_embed_url(self) -> str:
        """Return the URL to load in the web view."""
        ...  # pragma: no cover

    @abstractmethod
    def _get_non_embed_url(self) -> str:
        """Return the URL to load in the default browser."""
        ...  # pragma: no cover

    @classmethod
    @abstractmethod
    def _handle_auth_failure(cls) -> None:
        """Handle an authentication failure, e.g. prompt the user to log in."""
        ...  # pragma: no cover

    def _load_page(self) -> None:
        self.web.load_url(QUrl(self._get_embed_url()))
        qconnect(self.web.loadFinished, self._on_web_load_finished)

    def _on_web_load_finished(self, ok: bool) -> None:
        if not ok:
            LOGGER.error("Failed to load page.")  # pragma: no cover
            return  # pragma: no cover

        self._handle_auth_failure_if_needed()
        self._adjust_web_styling()

    def _handle_auth_failure_if_needed(self) -> None:
        def check_auth_failure_callback(value: str) -> None:
            if value.strip().endswith("Invalid token"):
                self._handle_auth_failure()

        self.web.evalWithCallback(
            "document.body.innerHTML", check_auth_failure_callback
        )

    def _adjust_web_styling(self) -> None:
        # Replace focus outline that QtWebEngine uses by default
        css = """
            :focus {
                outline: 2px !important;
            }
        """

        css_code = f"""
            var style = document.createElement('style');
            style.type = 'text/css';
            style.innerHTML = `{css}`;
            document.head.appendChild(style);
        """

        self.web.eval(css_code)


class AuthenticationRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info) -> None:
        token = config.token()
        if not token:
            return

        if config.app_url in info.requestUrl().toString():
            info.setHttpHeader(b"Authorization", b"Token " + token.encode())
