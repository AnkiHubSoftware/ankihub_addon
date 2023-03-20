from dataclasses import dataclass
from typing import Optional

from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QSize,
    Qt,
    QVBoxLayout,
    qconnect,
)

from ..ankihub_client import SuggestionType
from ..settings import RATIONALE_FOR_CHANGE_MAX_LENGTH, AnkiHubCommands


@dataclass
class SuggestionMetadata:
    comment: str
    auto_accept: bool
    change_type: SuggestionType


class SuggestionDialog(QDialog):
    silentlyClose = True

    def __init__(self, command):
        super().__init__()
        self.command = command

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("Note Suggestion(s)")

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.select = select = CustomListWidget()
        select.addItems([x.value[1] for x in SuggestionType])
        select.setCurrentRow(0)
        # Hide the change type options if it's a new card.
        if self.command != AnkiHubCommands.NEW.value:
            # change type select
            label = QLabel("Change Type")
            layout.addWidget(label)
            layout.addWidget(select)

        # comment field
        label = QLabel("Rationale for Change (Required)")
        layout.addWidget(label)

        self.edit = edit = QPlainTextEdit()

        def limit_length():
            while len(edit.toPlainText()) >= RATIONALE_FOR_CHANGE_MAX_LENGTH:
                edit.textCursor().deletePreviousChar()

        edit.textChanged.connect(limit_length)  # type: ignore
        layout.addWidget(edit)

        # "auto-accept" checkbox
        self.auto_accept_cb = QCheckBox("Submit without review (maintainers only).")
        layout.addWidget(self.auto_accept_cb)

        # button box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(button_box.accepted, self.accept)
        layout.addWidget(button_box)

        # disable save button when rationale for change field is empty
        button_box.setDisabled(True)

        def toggle_save_button_disabled_state():
            if len(edit.toPlainText().strip()) == 0:
                button_box.setDisabled(True)
            else:
                button_box.setDisabled(False)

        edit.textChanged.connect(toggle_save_button_disabled_state)  # type: ignore

    def run(self) -> Optional[SuggestionMetadata]:
        if not self.exec():
            return None

        return SuggestionMetadata(
            change_type=self._change_type(),
            comment=self._comment(),
            auto_accept=self._auto_accept(),
        )

    def _comment(self) -> str:
        return self.edit.toPlainText()

    def _change_type(self) -> Optional[SuggestionType]:
        if self.command == AnkiHubCommands.NEW.value:
            return None
        else:
            return next(
                x
                for x in SuggestionType
                if x.value[1] == self.select.currentItem().text()
            )

    def _auto_accept(self) -> bool:
        return self.auto_accept_cb.isChecked()


class CustomListWidget(QListWidget):
    def sizeHint(self) -> QSize:
        # adjusts height to content
        size = QSize()
        size.setHeight(self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth())
        return size
