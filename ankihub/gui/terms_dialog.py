from typing import Any, Optional

from aqt.qt import QColor, QUrl, QVBoxLayout
from aqt.webview import AnkiWebPage, AnkiWebView

from ..gui.webview import AlwaysOnTopOfParentDialog, AuthenticationRequestInterceptor


class TermsOfServiceDialog(AlwaysOnTopOfParentDialog):
    dialog: Optional["TermsOfServiceDialog"] = None

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        # TODO: Change the title of the dialog to the actual title of the terms of service window
        self.setWindowTitle("Terms")
        self.resize(1000, 800)

        self.web = AnkiWebView(parent=self)
        self.web.set_open_links_externally(False)
        self.interceptor = AuthenticationRequestInterceptor(self.web)
        page = AnkiWebPage(self.web._onBridgeCmd)
        page.open_links_externally = False
        self.web.setPage(page)
        self.web.page().profile().setUrlRequestInterceptor(self.interceptor)
        self.web.page().setBackgroundColor(QColor("white"))
        # TODO: Change the URL to the actual URL of the terms of service page
        self.web.page().setUrl(
            QUrl(
                "http://localhost:8000/ai/bf5e66e5-1149-4f3b-99bf-31cd95ad970a/flashcard-selector-embed/?is_on_anki=true"
            )
        )

        layout = QVBoxLayout()
        layout.addWidget(self.web)
        self.setLayout(layout)

    @classmethod
    def display(cls, parent: Any) -> "TermsOfServiceDialog":
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
