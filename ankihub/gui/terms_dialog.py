from typing import Any, Optional

from aqt.qt import QColor, QUrl, QVBoxLayout
from aqt.webview import AnkiWebPage, AnkiWebView

from ..gui.webview import AlwaysOnTopOfParentDialog, AuthenticationRequestInterceptor
from ..settings import config


class TermsAndConditionsDialog(AlwaysOnTopOfParentDialog):
    dialog: Optional["TermsAndConditionsDialog"] = None

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Settings")
        self.resize(1000, 800)

        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)
        self.interceptor = AuthenticationRequestInterceptor(self.web)
        page = AnkiWebPage(self.web._onBridgeCmd)
        page.open_links_externally = False
        self.web.setPage(page)
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)
        self.web.page().setBackgroundColor(QColor("white"))
        self.web.page().setUrl(QUrl(f"{config.app_url}/users/school-info-and-terms-and-conditions/"))

        layout = QVBoxLayout()
        layout.addWidget(self.web)
        self.setLayout(layout)

    @classmethod
    def display(cls, parent: Any) -> "TermsAndConditionsDialog":
        if cls.dialog:
            cls.dialog.close()
            cls.dialog = None

        if not cls.dialog:
            cls.dialog = cls(parent=parent)
            cls.dialog.show()

        return cls.dialog

    @classmethod
    def hide(cls):
        if cls.dialog:
            cls.dialog.close()
            cls.dialog = None
