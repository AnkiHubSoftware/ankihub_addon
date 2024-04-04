import uuid
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QEvent,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    qconnect,
)

if TYPE_CHECKING:
    from ..settings import BehaviorOnRemoteNoteDeleted  # pragma: no cover


class ConfigureDeletedNotesDialog(QDialog):
    """Dialog to configure the behavior when a remote note is deleted for each deck.

    This dialog shows a list of decks and a checkbox for each deck to configure the behavior.
    The dialog can't be closed using the close button in the title bar. It can only be closed by
    clicking the OK button. This ensures that the user configures the behavior for each deck before continuing.

    If show_new_feature_message is True, a message will be shown at the top of the dialog
    to inform the user about the note deletion feature.
    """

    def __init__(
        self,
        parent,
        deck_id_and_name_tuples: List[Tuple[uuid.UUID, str]],
        show_new_feature_message: bool = False,
        callback: Optional[
            Callable[[Dict[uuid.UUID, "BehaviorOnRemoteNoteDeleted"]], None]
        ] = None,
    ) -> None:
        super().__init__(parent)

        self._deck_id_and_name_tuples = deck_id_and_name_tuples
        self._show_new_feature_message = show_new_feature_message
        self._callback = callback
        self._setup_ui()

    def accept(self) -> None:
        deck_id_to_behavior = self.deck_id_to_behavior_on_remote_note_deleted_dict()

        super().accept()

        if self._callback:
            self._callback(deck_id_to_behavior)

    def deck_id_to_behavior_on_remote_note_deleted_dict(
        self,
    ) -> Dict[uuid.UUID, "BehaviorOnRemoteNoteDeleted"]:
        from ..settings import BehaviorOnRemoteNoteDeleted

        result: Dict[uuid.UUID, BehaviorOnRemoteNoteDeleted] = {}
        for i, (deck_id, _) in enumerate(self._deck_id_and_name_tuples, 1):
            checkbox_layout = self.grid_layout.itemAtPosition(i, 1).layout()
            delete_checkbox: QCheckBox = checkbox_layout.itemAt(1).widget()
            result[deck_id] = (
                BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
                if delete_checkbox.isChecked()
                else BehaviorOnRemoteNoteDeleted.NEVER_DELETE
            )
        return result

    def _setup_ui(self) -> None:
        self.setWindowTitle("AnkiHub | Configure deleted notes")

        self.new_feature_label = QLabel(
            "🌟 <b>New Feature!</b> Notes can now be deleted from AnkiHub.<br>"
            'Deck maintainers may approve suggestions to "delete notes,"<br>'
            "providing learners with the most concise and high quality decks."
        )

        self.top_label = QLabel(
            "When AnkiHub deletes notes that I have no review history with, they<br>"
            "should also be removed locally from these decks..."
        )

        self.scroll_area = self._setup_scroll_area()

        self.bottom_label = QLabel(
            "You can adjust this setting later in the <b>Deck Management</b> menu."
        )

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        qconnect(self.button_box.accepted, self.accept)

        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(20, 20, 20, 20)

        if self._show_new_feature_message:
            self.main_layout.addWidget(self.new_feature_label)
            self.main_layout.addSpacing(20)

        self.main_layout.addWidget(self.top_label)
        self.main_layout.addSpacing(10)
        self.main_layout.addWidget(self.scroll_area)
        self.main_layout.addSpacing(10)
        self.main_layout.addWidget(self.bottom_label)
        self.main_layout.addSpacing(25)
        self.main_layout.addWidget(self.button_box)

        self.setLayout(self.main_layout)

        # Fix the size of the dialog to prevent it from being resized.
        self.adjustSize()
        self.setFixedSize(self.size())

    def _setup_scroll_area(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.grid_layout = self._setup_grid_layout()
        self.scroll_widget.setLayout(self.grid_layout)
        self.scroll_area.setWidget(self.scroll_widget)
        return self.scroll_area

    def _setup_grid_layout(self) -> QGridLayout:
        self.grid_layout = QGridLayout()

        # Add headers to the grid layout
        self.deck_name_label = QLabel("<b>Deck name:</b>")
        self.delete_label = QLabel("<b>Delete:</b>")

        self.grid_layout.addWidget(self.deck_name_label, 0, 0)
        self.grid_layout.addWidget(self.delete_label, 0, 1)

        # Setup a row for each deck
        for i, (_, deck_name) in enumerate(self._deck_id_and_name_tuples):
            deck_label = QLabel(deck_name)
            deck_label.setWordWrap(True)

            deck_checkbox = QCheckBox()
            checkbox_layout = QHBoxLayout()
            checkbox_layout.addSpacing(20)
            checkbox_layout.addWidget(deck_checkbox)

            # Add deck label and checkbox to the row
            # Offset by 1 due to header row.
            self.grid_layout.addWidget(deck_label, i + 1, 0)
            self.grid_layout.addLayout(checkbox_layout, i + 1, 1)
            self.grid_layout.setRowMinimumHeight(i + 1, 40)

        self.grid_layout.setColumnStretch(0, 3)
        self.grid_layout.setColumnStretch(1, 1)
        return self.grid_layout

    def closeEvent(self, event: QEvent) -> None:
        # This prevents the dialog from being closed using the close button in the title bar.
        event.ignore()
