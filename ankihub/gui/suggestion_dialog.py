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

from ankihub.constants import RATIONALE_FOR_CHANGE_MAX_LENGTH, ChangeTypes

DISPLAY_NAME_TO_CHANGE_TYPE = {
    "New Update (from FF 2019+)": ChangeTypes.NEW_UPDATE,
    "Spelling/Grammatical": ChangeTypes.LANGUAGE_ERROR,
    "Content error": ChangeTypes.CONTENT_ERROR,
}


class SuggestionDialog(QDialog):
    def __init__(self):
        super().__init__()

        self.setup_ui()

    def setup_ui(self) -> None:
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout()
        self.setLayout(layout)

        # change type select
        label = QLabel("Change Type")
        layout.addWidget(label)

        self.select = select = CustomListWidget()
        select.addItems(DISPLAY_NAME_TO_CHANGE_TYPE.keys())
        select.setCurrentRow(0)
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
        return DISPLAY_NAME_TO_CHANGE_TYPE[self.select.currentItem().text()]


class CustomListWidget(QListWidget):
    def sizeHint(self) -> QSize:
        # adjusts height to content
        size = QSize()
        size.setHeight(self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth())
        return size
