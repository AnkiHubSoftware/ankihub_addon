from aqt.qt import (
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

from ..constants import RATIONALE_FOR_CHANGE_MAX_LENGTH, ChangeTypes, AnkiHubCommands


class SuggestionDialog(QDialog):
    def __init__(self, command):
        super().__init__()
        self.command = command

        self.setup_ui()

    def setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout()
        self.setLayout(layout)

        self.select = select = CustomListWidget()
        select.addItems([x.value[1] for x in ChangeTypes])
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

    def accept(self) -> None:
        return super().accept()

    def comment(self) -> str:
        return self.edit.toPlainText()

    def change_type(self) -> ChangeTypes:
        if self.command == AnkiHubCommands.NEW.value:
            return ChangeTypes.NEW_CARD_TO_ADD
        else:
            return next(
                x for x in ChangeTypes if x.value[1] == self.select.currentItem().text()
            )


class CustomListWidget(QListWidget):
    def sizeHint(self) -> QSize:
        # adjusts height to content
        size = QSize()
        size.setHeight(self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth())
        return size
