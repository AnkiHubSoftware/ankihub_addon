import aqt
import aqt.sync
from aqt.qt import (
    QCheckBox,
    QColor,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    Qt,
    QTextEdit,
    QVBoxLayout,
    qconnect,
)

from ..gui.webview import AlwaysOnTopOfParentDialog
from ..main.exceptions import ChangesRequireFullSyncError
from .utils import CollapsibleSection


class ChangesRequireFullSyncDialog(AlwaysOnTopOfParentDialog):
    def __init__(
        self,
        changes_require_full_sync_error: ChangesRequireFullSyncError,
        parent,
    ):
        super().__init__(parent)
        self.setWindowTitle(" ")
        self.setMinimumWidth(400)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(8)

        # Title inside the dialog
        title_label = QLabel("<h3>Some changes require a full sync</h3>")
        title_label.setWordWrap(True)
        main_layout.addWidget(title_label)
        main_layout.addSpacing(20)

        # Collapsible Note Type Updates Section, with a maximum expanded height.
        collapsible = CollapsibleSection("Note type updates", expanded_max_height=160)
        collapsible.toggle_button.setStyleSheet(
            collapsible.toggle_button.styleSheet() + "QToolButton { color: gray; }"
        )

        # Layout for the collapsible content
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(5, 5, 5, 5)
        content_layout.setSpacing(5)

        self.note_updates_text = QTextEdit()
        self.note_updates_text.setTextColor(QColor("#808080"))
        self.note_updates_text.setText(
            "\n".join(
                aqt.mw.col.models.get(mid)["name"]
                for mid in changes_require_full_sync_error.affected_note_type_ids
            )
        )
        self.note_updates_text.setReadOnly(True)
        self.note_updates_text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        content_layout.addWidget(self.note_updates_text)

        collapsible.setContentLayout(content_layout)
        main_layout.addWidget(collapsible)
        main_layout.addSpacing(20)

        # Warning label
        warning_label = QLabel(
            "⚠️ <b>Prevent data loss:</b> make sure all your devices are synced with AnkiWeb before proceeding."
        )
        warning_label.setWordWrap(True)
        main_layout.addWidget(warning_label)
        main_layout.addSpacing(10)

        # Checkbox to enable the full sync button
        self.synced_checkbox = QCheckBox("I have synced my devices")
        main_layout.addWidget(self.synced_checkbox)
        main_layout.addSpacing(20)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(separator)
        main_layout.addSpacing(20)

        # Mobile instructions label
        mobile_instructions = QLabel(
            "👉 <b>On mobile</b>, after full sync, select the appropriate option when prompted:"
            "<ul>"
            "<li><b>iOS</b>: “Download from AnkiWeb”</li>"
            "<li><b>Android</b>: “AnkiWeb” or “Keep AnkiWeb collection”<br></li>"
            "</ul>"
        )
        mobile_instructions.setWordWrap(True)
        main_layout.addWidget(mobile_instructions)

        main_layout.addStretch()

        # Buttons layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.skip_button = QPushButton("Skip for now")
        self.run_full_sync_button = QPushButton("Run Full Sync")
        self.update_run_full_sync_button()

        self.skip_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.run_full_sync_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        button_layout.addWidget(self.skip_button)
        button_layout.addWidget(self.run_full_sync_button)
        main_layout.addLayout(button_layout)

        # Enable/disable the Run Full Sync button based on checkbox state.
        qconnect(
            (
                self.synced_checkbox.checkStateChanged
                if hasattr(self.synced_checkbox, "checkStateChanged")
                else self.synced_checkbox.stateChanged
            ),
            lambda *_: self.update_run_full_sync_button(),
        )

        qconnect(self.skip_button.clicked, self.reject)
        qconnect(self.run_full_sync_button.clicked, self.accept)

        self.setLayout(main_layout)

    def update_run_full_sync_button(self):
        is_checked = self.synced_checkbox.isChecked()
        self.run_full_sync_button.setEnabled(is_checked)
        self.run_full_sync_button.setStyleSheet(
            "color: white; background-color: #306bec" if is_checked else ""
        )
