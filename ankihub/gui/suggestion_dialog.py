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

from ankihub.constants import COMMENT_MAX_LENGTH, ChangeTypes

CHANGE_TYPE_TO_DISPLAY_NAME = {
    ChangeTypes.NEW_UPDATE: "New Update (from FF 2019+)",
    ChangeTypes.LANGUAGE_ERROR: "Spelling/Grammatical",
    ChangeTypes.CONTENT_ERROR: "Content error",
}
assert set(CHANGE_TYPE_TO_DISPLAY_NAME.keys()) == set(list(ChangeTypes))


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
        select.addItems([CHANGE_TYPE_TO_DISPLAY_NAME[x] for x in ChangeTypes])
        select.setCurrentRow(0)
        layout.addWidget(select)

        # comment field
        label = QLabel("Rationale for Change (Required)")
        layout.addWidget(label)

        self.edit = edit = QPlainTextEdit()

        def limit_length():
            while len(edit.toPlainText()) >= COMMENT_MAX_LENGTH:
                edit.textCursor().deletePreviousChar()

        edit.textChanged.connect(limit_length)  # type: ignore
        layout.addWidget(edit)

        # button box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(button_box.accepted, self.accept)
        layout.addWidget(button_box)

    def accept(self) -> None:
        return super().accept()

    def comment(self) -> str:
        return self.edit.toPlainText()

    def change_type(self) -> ChangeTypes:
        return next(
            x
            for x in ChangeTypes
            if CHANGE_TYPE_TO_DISPLAY_NAME[x] == self.select.currentItem().text()
        )


class CustomListWidget(QListWidget):
    def sizeHint(self) -> QSize:
        # adjusts height to content
        size = QSize()
        size.setHeight(self.sizeHintForRow(0) * self.count() + 2 * self.frameWidth())
        return size
