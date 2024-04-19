from abc import abstractmethod
from typing import Any, Optional, cast

from aqt.qt import (
    QColor,
    QDialog,
    QUrl,
    QVBoxLayout,
    QWebEngineUrlRequestInterceptor,
    qconnect,
)
from aqt.theme import theme_manager
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
        self.web.page().setBackgroundColor(QColor("white"))

        self.interceptor = AuthenticationRequestInterceptor()
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)

        self.layout_ = QVBoxLayout()
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.web)

        self.setLayout(self.layout_)

    @abstractmethod
    def _get_url(self) -> QUrl:
        """Return the URL to load in the web view."""
        ...  # pragma: no cover

    @classmethod
    @abstractmethod
    def _handle_auth_failure(cls) -> None:
        """Handle an authentication failure, e.g. prompt the user to log in."""
        ...  # pragma: no cover

    def _load_page(self) -> None:

        if theme_manager.get_night_mode():
            self.web.eval("localStorage.setItem('theme', 'dark')")
        else:
            self.web.eval("localStorage.removeItem('theme')")

        self.web.load_url(self._get_url())
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
