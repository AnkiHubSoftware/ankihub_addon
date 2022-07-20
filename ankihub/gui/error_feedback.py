from aqt.qt import QDialog, QVBoxLayout
from aqt.webview import AnkiWebView
from aqt.utils import restoreGeom, saveGeom, disable_help_button

from ..messages import messages


class ErrorFeedbackDialog(QDialog):
    GEOM_KEY = "Ankihub-Errorfeedback"

    def __init__(self, event_id: str) -> None:
        QDialog.__init__(self)
        self.event_id = event_id
        self.setup()

    def setup(self) -> None:
        disable_help_button(self)
        self.setMinimumWidth(680)
        self.setMinimumHeight(500)
        restoreGeom(self, self.GEOM_KEY)
        self.setWindowTitle("Ankihub Error Feedback")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = AnkiWebView(self, title="AnkiHub Error")
        self.web.stdHtml(body=messages.request_error(event_id=self.event_id))
        self.web.set_bridge_command(self.link_handler, self)

        layout.addWidget(self.web)
        self.setLayout(layout)

    def link_handler(self, url: str) -> None:
        if url == "close":
            self.accept()

    def accept(self) -> None:
        saveGeom(self, self.GEOM_KEY)
        QDialog.accept(self)
