from abc import abstractmethod
from typing import Any, Callable

from aqt import QWebEnginePage, QWebEngineProfile, pyqtSlot
from aqt.gui_hooks import theme_did_change
from aqt.qt import (
    QColor,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QUrl,
    QVBoxLayout,
    QWebEngineUrlRequestInterceptor,
    QWidget,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import openLink
from aqt.webview import AnkiWebPage, AnkiWebView

from .. import LOGGER
from ..settings import config
from .utils import using_qt5


class AnkiHubWebViewDialog(QDialog):
    """A dialog that displays a web view. The purpose is to show an AnkiHub web app page.
    This class handles setting up the web view, loading the page, styling and authentication.
    """

    def __init__(self, parent: Any) -> None:
        super().__init__(parent)
        self._setup_ui()

    def display(self) -> bool:
        """Display the dialog. Return True if the initialization was successful, False otherwise."""
        if not config.token():
            self._handle_auth_failure()
            return False

        self._load_page()
        self.activateWindow()
        self.raise_()
        self.show()

        return True

    def _setup_ui(self) -> None:
        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)

        self.interceptor = AuthenticationRequestInterceptor()
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)
        self.web.page().setBackgroundColor(QColor("white"))

        # Set the context to self so that self gets passed as context to the webview_did_receive_js_message hook
        # when a pycmd is called from the web view.
        self.web.set_bridge_command(func=self.web.defaultOnBridgeCmd, context=self)

        theme_did_change.append(self._update_page_theme)

        self.view_in_web_browser_button = QPushButton("View in web browser")
        self.view_in_web_browser_button.setAutoDefault(False)
        qconnect(
            self.view_in_web_browser_button.clicked,
            self._on_view_in_web_browser_button_clicked,
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
        self.layout_.setContentsMargins(0, 0, 0, 5)
        self.layout_.addWidget(self.web)
        self.layout_.addLayout(self.button_layout)

        self.setLayout(self.layout_)

    @abstractmethod
    def _get_embed_url(self) -> str:
        """Return the URL to load in the web view."""
        ...  # pragma: no cover

    @abstractmethod
    def _get_non_embed_url(self) -> str:
        """Return the URL to load in the default browser."""
        ...  # pragma: no cover

    @abstractmethod
    def _handle_auth_failure(self) -> None:
        """Handle an authentication failure, e.g. prompt the user to log in."""
        ...  # pragma: no cover

    def _on_successful_page_load(self) -> None:
        ...  # pragma: no cover

    def _load_page(self) -> None:
        self._update_page_theme()
        self.web.load_url(QUrl(self._get_embed_url()))
        # Allow drag and drop event
        self.web.allow_drops = True
        qconnect(self.web.loadFinished, self._on_web_load_finished)

    def _update_page_theme(self) -> None:
        if theme_manager.get_night_mode():
            self.web.eval("localStorage.setItem('theme', 'dark')")
        else:
            self.web.eval("localStorage.setItem('theme', 'light')")

    def _on_web_load_finished(self, ok: bool) -> None:
        self._handle_auth_failure_if_needed()

        if not ok:
            LOGGER.error("Failed to load page.")  # pragma: no cover
            return  # pragma: no cover

        self._adjust_web_styling()
        self._on_successful_page_load()

    def _handle_auth_failure_if_needed(self) -> None:
        def check_auth_failure_callback(value: str) -> None:
            if value.strip().endswith("Invalid token"):
                self._handle_auth_failure()

        self.web.evalWithCallback(
            "document.body.innerHTML", check_auth_failure_callback
        )

    def _adjust_web_styling(self) -> None:
        css = """
            /* Replace focus outline that QtWebEngine uses by default */
            :focus {
                outline: 2px !important;
            }

            /* Fix checkbox styling */
            input[type="checkbox"]:checked::before {
                background-color: initial;
                transform: initial;
                clip-path: initial;
                content: "";
                position: absolute;
                left: 5px;
                top: 1px;
                width: 5px;
                height: 10px;
                border: solid white;
                border-width: 0 2px 2px 0;
                -webkit-transform: rotate(45deg);
                -ms-transform: rotate(45deg);
                transform: rotate(45deg);
            }
            input[type="checkbox"]:indeterminate::before {
                background-color: initial;
                transform: initial;
                clip-path: initial;
                content: "";
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                background-color: #fff;
                width: 7px;
                height: 2px;
            }
        """

        if using_qt5():
            css += """
                /* Fix range input styling */
                input[type="range"]::-webkit-slider-thumb {
                    margin-top: -7px
                }
            """

        css_code = f"""
            var style = document.createElement('style');
            style.type = 'text/css';
            style.innerHTML = `{css}`;
            document.head.appendChild(style);
        """

        self.web.eval(css_code)

    def _on_view_in_web_browser_button_clicked(self) -> None:
        openLink(self._get_non_embed_url())
        self.close()


class AuthenticationRequestInterceptor(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info) -> None:
        token = config.token()
        if not token:
            return

        if config.app_url in info.requestUrl().toString():
            info.setHttpHeader(b"Authorization", b"Token " + token.encode())


class CustomWebPage(AnkiWebPage):
    """
    AnkiWebPage which grants the `Notifications` feature permission.
    """

    def __init__(
        self,
        parent: QWidget,
        profile: QWebEngineProfile,
        onBridgeCmd: Callable[[str], Any],
    ):
        QWebEnginePage.__init__(self, profile, parent)
        self._onBridgeCmd = onBridgeCmd
        self._setupBridge()
        self.open_links_externally = False
        qconnect(self.featurePermissionRequested, self.handlePermissionRequested)  # type: ignore

    @pyqtSlot(QUrl, QWebEnginePage.Feature)
    def handlePermissionRequested(
        self, securityOrigin: QUrl, feature: QWebEnginePage.Feature
    ) -> None:
        # Without this logging into Boards and Beyond doesn't work.
        if feature == QWebEnginePage.Feature.Notifications:
            self.setFeaturePermission(
                securityOrigin,
                feature,
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser,
            )
        else:
            self.setFeaturePermission(
                securityOrigin,
                feature,
                QWebEnginePage.PermissionPolicy.PermissionDeniedByUser,
            )
