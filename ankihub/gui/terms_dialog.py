from typing import Any, Callable, Optional

from aqt import sip
from aqt.qt import (
    QColor,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    Qt,
    QTimer,
    QUrl,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import openLink
from aqt.webview import AnkiWebPage, AnkiWebView

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient
from ..gui.webview import AuthenticationRequestInterceptor
from ..settings import config
from .operations import AddonQueryOp
from .utils import using_qt5

# How often, and for how long, to auto-poll for terms acceptance after opening the page in the
# external browser. After the window elapses, auto-polling stops and the manual "Check now" button
# remains as the fallback (see NRT-822).
_AUTO_POLL_INTERVAL_MS = 3_000
_AUTO_POLL_WINDOW_MS = 3 * 60 * 1_000


def _terms_url() -> str:
    return f"{config.app_url}/users/school-info-and-terms-and-conditions/"


class TermsAndConditionsDialog(QDialog):
    dialog: Optional["TermsAndConditionsDialog"] = None
    # The Qt5 variant, which opens the page in the external browser instead of the embedded webview.
    external_dialog: Optional["ExternalBrowserTermsDialog"] = None

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("Settings")
        self.resize(1000, 800)

        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)
        self.interceptor = AuthenticationRequestInterceptor(self.web)
        page = AnkiWebPage(self.web._onBridgeCmd)
        page.setParent(self)
        page.open_links_externally = False
        self.web.setPage(page)
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)
        self.web.page().setBackgroundColor(QColor("white"))
        self.web.page().setUrl(QUrl(_terms_url()))

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.setLayout(layout)

    @classmethod
    def display(cls, parent: Any, on_accepted: Optional[Callable[[], None]] = None) -> None:
        """Display the terms & conditions for the user to accept.

        On Qt5 builds the embedded Chromium-77 webview can't render the page, so it is opened in the
        user's external browser and acceptance is detected via polling instead (see NRT-822).
        ``on_accepted`` is invoked (Qt5 path only) once acceptance is detected, to resume the action
        that was blocked by the missing agreement.
        """
        if using_qt5():
            cls._display_external(parent=parent, on_accepted=on_accepted)
            return

        if not cls.dialog:
            cls.dialog = cls(parent=parent)

        cls.dialog.show()

    @classmethod
    def _display_external(cls, parent: Any, on_accepted: Optional[Callable[[], None]]) -> None:
        openLink(_terms_url())

        if cls.external_dialog and not sip.isdeleted(cls.external_dialog):
            cls.external_dialog.activateWindow()
            cls.external_dialog.raise_()
            return

        cls.external_dialog = ExternalBrowserTermsDialog(parent=parent, on_accepted=on_accepted)
        cls.external_dialog.show()

    @classmethod
    def hide(cls):
        if cls.dialog:
            cls.dialog.close()
            cls.dialog = None
        if cls.external_dialog:
            if not sip.isdeleted(cls.external_dialog):
                cls.external_dialog.close()
            cls.external_dialog = None


class ExternalBrowserTermsDialog(QDialog):
    """Shown on Qt5 builds, where the embedded webview can't render the terms page.

    The terms page is opened in the system browser; this dialog waits for the user to accept there.
    Acceptance is detected by auto-polling AnkiHub for a bounded window, with a manual "Check now"
    button as the fallback once the window elapses. Both paths share the same check + resume logic.
    """

    def __init__(self, parent: Any = None, on_accepted: Optional[Callable[[], None]] = None) -> None:
        super().__init__(parent)
        self._on_accepted = on_accepted
        self._is_checking = False
        self._setup_ui()
        self._start_auto_poll()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("AnkiHub | Terms & Conditions")

        message = QLabel(
            "Please accept the AnkiHub Terms &amp; Conditions in the web browser window that just "
            "opened, then return here.<br><br>"
            "This window will close automatically once acceptance is detected. If it doesn't, click "
            "<b>Check now</b>."
        )
        message.setWordWrap(True)
        message.setTextFormat(Qt.TextFormat.RichText)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.RichText)

        self.open_again_button = QPushButton("Open page again")
        self.open_again_button.setAutoDefault(False)
        qconnect(self.open_again_button.clicked, lambda: openLink(_terms_url()))

        self.check_now_button = QPushButton("Check now")
        self.check_now_button.setDefault(True)
        qconnect(self.check_now_button.clicked, lambda: self._check_acceptance(is_manual=True))

        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        qconnect(self.close_button.clicked, self.close)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.open_again_button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)
        button_layout.addWidget(self.check_now_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 15)
        layout.addWidget(message)
        layout.addWidget(self.status_label)
        layout.addSpacing(10)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.setMinimumWidth(440)
        self.check_now_button.setFocus()

    def _start_auto_poll(self) -> None:
        self._auto_poll_timer = QTimer(self)
        qconnect(self._auto_poll_timer.timeout, lambda: self._check_acceptance(is_manual=False))
        self._auto_poll_timer.start(_AUTO_POLL_INTERVAL_MS)

        # Stop auto-polling once the window elapses; the manual "Check now" button remains.
        QTimer.singleShot(_AUTO_POLL_WINDOW_MS, self._stop_auto_poll)

    def _stop_auto_poll(self) -> None:
        if sip.isdeleted(self):
            return
        if self._auto_poll_timer.isActive():
            self._auto_poll_timer.stop()
            self.status_label.setText("Still waiting? Accept the terms in your browser, then click <b>Check now</b>.")

    def _check_acceptance(self, *, is_manual: bool) -> None:
        if self._is_checking:
            return
        self._is_checking = True

        if is_manual:
            self.status_label.setText("Checking…")

        def on_done(accepted: bool) -> None:
            self._is_checking = False
            if sip.isdeleted(self):
                return
            if accepted:
                self._on_acceptance_detected()
            elif is_manual:
                self.status_label.setText(
                    "Not accepted yet. Finish accepting in your browser, then click <b>Check now</b>."
                )

        def on_failure(exc: Exception) -> None:
            self._is_checking = False
            LOGGER.warning("Failed to check terms acceptance.", exc_info=exc)
            if sip.isdeleted(self):
                return
            if is_manual:
                self.status_label.setText("Couldn't check right now. Please try again in a moment.")

        AddonQueryOp(
            op=lambda _: AddonAnkiHubClient().is_terms_agreement_accepted(),
            success=on_done,
            parent=self,
        ).without_collection().failure(on_failure).run_in_background()

    def _on_acceptance_detected(self) -> None:
        if self._auto_poll_timer.isActive():
            self._auto_poll_timer.stop()
        LOGGER.info("Terms acceptance detected; closing external-browser terms dialog.")
        self.close()
        TermsAndConditionsDialog.external_dialog = None
        if self._on_accepted:
            self._on_accepted()
