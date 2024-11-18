from abc import abstractmethod
from pathlib import Path
from typing import Any, Callable

from anki.utils import is_mac
from aqt import QSizePolicy, QSplitter, QWebEnginePage, QWebEngineProfile, pyqtSlot
from aqt.gui_hooks import theme_did_change
from aqt.qt import (
    QCloseEvent,
    QColor,
    QDialog,
    QEvent,
    QHBoxLayout,
    QObject,
    QPushButton,
    QUrl,
    QVBoxLayout,
    QWebEngineUrlRequestInterceptor,
    QWidget,
    qconnect,
)
from aqt.reviewer import Reviewer
from aqt.utils import openLink
from aqt.webview import AnkiWebPage, AnkiWebView
from jinja2 import Template

from .. import LOGGER
from ..settings import config
from .utils import using_qt5

MH_INTEGRATION_TABS_TEMPLATE_PATH = (
    Path(__file__).parent / "web/mh_integration_tabs.html"
)


class AlwaysOnTopOfParentDialog(QDialog):
    """A dialog that is always on top of its parent window. This is useful on MacOS, where we had issues
    with dialogs hiding behind the parent window."""

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        if parent:
            parent.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched == self.parent() and event.type() == QEvent.Type.WindowActivate:
            self.raise_()
            self.activateWindow()
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.parent():
            self.parent().removeEventFilter(self)
        super().closeEvent(event)


class AnkiHubWebViewDialog(AlwaysOnTopOfParentDialog):
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

        # Set the background color of the web view back to white when Anki's theme changes it to dark
        theme_did_change.append(
            lambda: self.web.page().setBackgroundColor(QColor("white"))
        )

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
                border-color: #cccccc;
            }
            """
            + (
                # On mac, the background color is already white, so we don't need to set it.
                # Setting the background color to white on mac causes the dialog to loose its
                # native look (rounded borders).
                """
                QPushButton {
                    background-color: #fcfcfc;
                }
                """
                if not is_mac
                else ""
            )
        )

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
        self.web.load_url(QUrl(self._get_embed_url()))
        qconnect(self.web.loadFinished, self._on_web_load_finished)

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


class PrivateWebPage(AnkiWebPage):
    def __init__(self, profile: QWebEngineProfile, onBridgeCmd: Callable[[str], Any]):
        QWebEnginePage.__init__(self, profile, None)
        self._onBridgeCmd = onBridgeCmd
        self._setupBridge()
        self.open_links_externally = False
        self.featurePermissionRequested.connect(self.handlePermissionRequested)

    @pyqtSlot(QUrl, QWebEnginePage.Feature)
    def handlePermissionRequested(
        self, securityOrigin: QUrl, feature: QWebEnginePage.Feature
    ) -> None:
        if feature == QWebEnginePage.Feature.Notifications:
            self.setFeaturePermission(
                securityOrigin,
                feature,
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser,
            )
        else:
            super().featurePermissionRequested(securityOrigin, feature)


class SplitScreenWebViewManager:
    def __init__(self, reviewer: Reviewer, urls_list):
        self.reviewer = reviewer
        self.splitter = None
        self.webview = None
        self.current_active_url = urls_list[0]["url"]
        self.is_webview_visible = False
        self.urls_list = urls_list
        self._setup_webview()

    def _setup_webview(self):
        parent_widget = self.reviewer.mw

        if parent_widget is None:
            raise ValueError(
                "Reviewer does not have a parent widget to hold the splitter."
            )

        self.splitter = QSplitter()

        # Create a QWebEngineProfile with persistent storage
        profile = QWebEngineProfile("AnkiHubProfile", parent_widget)
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"
        )

        # Create the main web view
        self.webview = AnkiWebView()
        self.webview.setPage(PrivateWebPage(profile, self.webview._onBridgeCmd))
        self.webview.setUrl(QUrl(self.urls_list[0]["url"]))
        self.webview.set_bridge_command(self._on_bridge_cmd, self)

        # Interceptor that will add the token to the request
        interceptor = AuthenticationRequestInterceptor(self.webview)
        self.webview.page().profile().setUrlRequestInterceptor(interceptor)

        layout = parent_widget.layout()
        if layout is None:
            layout = QVBoxLayout(parent_widget)
            parent_widget.setLayout(layout)

        layout.addWidget(self.splitter)
        self.splitter.setSizePolicy(
            QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        )

        widget_index = parent_widget.mainLayout.indexOf(self.reviewer.web)
        parent_widget.mainLayout.removeWidget(self.reviewer.web)

        self.splitter.addWidget(self.reviewer.web)
        self.splitter.addWidget(self.webview)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([10000, 10000])

        parent_widget.mainLayout.insertWidget(widget_index, self.splitter)
        self.is_webview_visible = True
        qconnect(self.webview.loadFinished, self._inject_header)

    def toggle_inner_webviews(self):
        if self.is_webview_visible:
            self.hide_inner_webviews()
        else:
            self.show_inner_webviews()

    def show_inner_webviews(self):
        if not self.is_webview_visible:
            self.webview.show()
            self.is_webview_visible = True

    def hide_inner_webviews(self):
        if self.is_webview_visible:
            self.webview.hide()
            self.is_webview_visible = False

    def _on_bridge_cmd(self, cmd: str) -> None:
        cmd_name = cmd.split("::")[0]
        args = cmd.split("::")[1].split(",")
        if cmd_name == "updateWebviewWithURL":
            self._update_webview_url(args[0])

    def _inject_header(self, ok: bool):
        if not ok:
            LOGGER.error("Failed to load page.")  # pragma: no cover
            return  # pragma: no cover

        html_template = Template(MH_INTEGRATION_TABS_TEMPLATE_PATH.read_text()).render(
            {
                "tabs": self.urls_list,
                "current_active_tab_url": self.current_active_url,
                "page_title": "Boards&Beyond viewer",
            }
        )

        # JavaScript to inject the header into the loaded page
        js_code = f"""
            var wrapper = document.createElement('div');
            while (document.body.firstChild) {{
                wrapper.appendChild(document.body.firstChild);
            }}
            document.body.appendChild(wrapper);

            function selectTab(element) {{
                document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('selected'));
                element.classList.add('selected');
            }}

            var header = document.createElement('div');
            header.innerHTML = `{html_template}`;
            document.body.insertBefore(header, document.body.firstChild);
        """
        self.webview.eval(js_code)

    def _update_webview_url(self, url):
        if self.webview:
            self.webview.setUrl(QUrl(url))
            self.current_active_url = url


split_screen_webview_manager = None
