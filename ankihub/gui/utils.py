import inspect
import uuid
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import aqt
from aqt.addons import check_and_prompt_for_updates
from aqt.qt import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QIcon,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyle,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import disable_help_button, tooltip

from ..settings import config


def show_error_dialog(message: str, title: str, *args, **kwargs) -> None:
    aqt.mw.taskman.run_on_main(  # type: ignore
        lambda: show_dialog(message, title=title, icon=warning_icon(), *args, **kwargs)  # type: ignore
    )


def show_tooltip(message: str, parent=aqt.mw, *args, **kwargs) -> None:
    """Safer version of aqt.utils.tooltip that...
    - runs the tooltip function on the main thread
    - doesn't cause an error if the parent widget is deleted and instead shows
    the tooltip on the active or main window.

    Note: all parameters accepted by aqt.utils.tooltip can also be passed in the function call
    """
    aqt.mw.taskman.run_on_main(
        lambda: _show_tooltip(message, *args, parent=parent, **kwargs)
    )


def _show_tooltip(message: str, *args, **kwargs) -> None:
    try:
        tooltip(message, *args, **kwargs)
    except RuntimeError as e:
        if "wrapped C/C++ object of type" in str(e) and "has been deleted" in str(e):
            tooltip(message)
        else:
            raise e


def choose_subset(
    prompt: str,
    choices: List[str],
    current: List[str] = [],
    adjust_height_to_content=True,
    description_html: Optional[str] = None,
    parent: Any = None,
) -> Optional[List[str]]:
    if not parent:
        parent = aqt.mw.app.activeWindow()

    dialog = QDialog(parent)
    disable_help_button(dialog)

    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    layout = QVBoxLayout()
    dialog.setLayout(layout)
    label = QLabel(prompt)
    label.setOpenExternalLinks(True)
    layout.addWidget(label)
    list_widget = CustomListWidget()
    layout.addWidget(list_widget)
    layout.addSpacing(5)

    # add a "select all" button
    def select_all():
        all_selected = all(
            list_widget.item(i).checkState() == Qt.CheckState.Checked
            for i in range(list_widget.count())
        )
        if all_selected:
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        else:
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.CheckState.Checked)

    button = QPushButton("Select All")
    qconnect(button.clicked, select_all)
    layout.addWidget(button)
    for choice in choices:
        item = QListWidgetItem(choice)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)  # type: ignore
        item.setCheckState(
            Qt.CheckState.Checked if choice in current else Qt.CheckState.Unchecked
        )
        list_widget.addItem(item)

    if description_html:
        label = QLabel(f"<i>{description_html}</i>")
        layout.addWidget(label)

    layout.addSpacing(10)

    button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    qconnect(button_box.accepted, dialog.accept)
    layout.addWidget(button_box)

    if adjust_height_to_content:
        list_widget.setMinimumHeight(
            list_widget.sizeHintForRow(0) * list_widget.count() + 20
        )

    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    result = [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).checkState() == Qt.CheckState.Checked
    ]
    return result


class CustomListWidget(QListWidget):
    """A QListWidget that allows the user to toggle the checkbox by clicking anywhere on the item.
    Its ListWidget items are also not highlighted when selected."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet(
            """
            QListWidget::item:selected {
                background: palette(base);
            }
            """
        )

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        item = self.itemAt(event.pos())
        if item:
            if item.checkState() == Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.pos())
        if item and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return
        super().mouseReleaseEvent(event)


def choose_list(
    prompt: str, choices: list[str], startrow: int = 0, parent: Any = None
) -> Optional[int]:
    # adapted from aqt.utils.chooseList
    if not parent:
        parent = aqt.mw.app.activeWindow()
    d = QDialog(parent)
    disable_help_button(d)
    d.setWindowModality(Qt.WindowModality.WindowModal)
    layout = QVBoxLayout()
    d.setLayout(layout)
    t = QLabel(prompt)
    layout.addWidget(t)
    c = QListWidget()
    c.addItems(choices)
    c.setCurrentRow(startrow)
    layout.addWidget(c)
    bb = QDialogButtonBox()
    bb.addButton(QDialogButtonBox.StandardButton.Cancel)
    bb.addButton(QDialogButtonBox.StandardButton.Ok)
    qconnect(bb.rejected, d.reject)
    qconnect(bb.accepted, d.accept)
    layout.addWidget(bb)
    if d.exec() == QDialog.DialogCode.Accepted:
        return c.currentRow()
    else:
        return None


def choose_ankihub_deck(
    prompt: str, parent: QWidget, ah_dids: Optional[List[uuid.UUID]] = None
) -> Optional[uuid.UUID]:
    """Ask the user to choose a deck from the list of decks that the user subscribed to from the add-on.
    Returns the deck ID of the chosen deck, or None if none.

    If 'ah_dids' param is provided, only the decks with those UUIDS will be listed.
    When left as None (default value), all subscribed decks will be displayed.
    """

    ah_dids = ah_dids or config.deck_ids()
    deck_configs = [
        (config.deck_config(did), did) for did in ah_dids if did in config.deck_ids()
    ]
    chosen_deck_idx = choose_list(
        prompt=prompt,
        choices=[deck.name for deck, _ in deck_configs],
        parent=parent,
    )

    if chosen_deck_idx is None:
        return None

    chosen_deck_ah_did = deck_configs[chosen_deck_idx][1]
    return chosen_deck_ah_did


def ask_user(
    text: str,
    parent: Optional[QWidget] = None,
    default_no: bool = False,
    title: str = "Anki",
    show_cancel_button: bool = False,
    yes_button_label: str = "Yes",
    no_button_label: str = "No",
) -> Optional[bool]:
    "Show a yes/no question. Return true if yes. Return false if no. Return None if cancelled."

    if not parent:
        parent = aqt.mw.app.activeWindow()

    msg = QMessageBox(parent=parent)
    msg.setWindowTitle(title)
    msg.setTextFormat(Qt.TextFormat.RichText)
    msg.setText(text)

    yes_button = msg.addButton(
        yes_button_label,
        QMessageBox.ButtonRole.YesRole,
    )
    no_button = msg.addButton(
        no_button_label,
        QMessageBox.ButtonRole.NoRole,
    )
    if show_cancel_button:
        msg.addButton(
            "Cancel",
            QMessageBox.ButtonRole.RejectRole,
        )

    msg.setDefaultButton(no_button if default_no else yes_button)
    msg.setIcon(QMessageBox.Icon.Question)

    msg.exec()

    if msg.clickedButton() == yes_button:
        return True
    elif msg.clickedButton() == no_button:
        return False
    else:
        return None


ButtonParam = Union[
    QDialogButtonBox.StandardButton,
    str,
    Tuple[str, QDialogButtonBox.ButtonRole],
]


class _Dialog(QDialog):
    """A simple dialog with a text and buttons. The dialog closes when a button is clicked and
    the callback is called with the index of the clicked button.
    This class is intended to be used via with the show_dialog function.
    """

    def __init__(
        self,
        parent: QWidget,
        text: str,
        title: str,
        text_format: Qt.TextFormat,
        buttons: Optional[Sequence[ButtonParam]],
        default_button_idx: int,
        scrollable: bool,
        callback: Optional[Callable[[int], None]],
        icon: Optional[QIcon],
    ) -> None:
        super().__init__(parent)

        self.text = text
        self.title = title
        self.text_format = text_format
        self.buttons = buttons
        self.default_button_idx = default_button_idx
        self.scrollable = scrollable
        self.callback = callback
        self.icon = icon
        self._is_closing = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle(self.title)
        disable_help_button(self)

        self.outer_layout = QHBoxLayout(self)

        self.icon_layout = QVBoxLayout()
        self.outer_layout.addLayout(self.icon_layout)

        self.outer_layout.addSpacing(20)

        # Contains the text and the buttons
        self.main_layout = QVBoxLayout()
        self.outer_layout.addLayout(self.main_layout)

        if self.icon is not None:
            icon_label = QLabel()
            icon_label.setPixmap(self.icon.pixmap(48, 48))
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.icon_layout.addWidget(icon_label)

            self.icon_layout.addStretch()

        if self.scrollable:
            area = QScrollArea()
            area.setWidgetResizable(True)
            widget = QWidget()
            area.setWidget(widget)
            self.main_layout.addWidget(area)
            self.content_layout = QVBoxLayout(widget)
        else:
            self.content_layout = self.main_layout

        label = QLabel(self.text)
        label.setWordWrap(True)
        label.setTextFormat(self.text_format)
        self.content_layout.addWidget(label)

        self.content_layout.addSpacing(10)

        self.button_box = self._setup_button_box()

        self.main_layout.addStretch()
        self.main_layout.addWidget(self.button_box)

    def _setup_button_box(self) -> QDialogButtonBox:
        button_box = QDialogButtonBox()

        self.default_button = None
        for button_index, button in enumerate(self.buttons):
            if isinstance(button, str):
                button = button_box.addButton(
                    button, QDialogButtonBox.ButtonRole.ActionRole
                )
            elif isinstance(button, QDialogButtonBox.StandardButton):
                button = button_box.addButton(button)
            elif isinstance(button, tuple):
                button = button_box.addButton(*button)

            qconnect(
                button.clicked,
                partial(self._on_btn_clicked_or_dialog_rejected, button_index),
            )

            if button_index == self.default_button_idx:
                self.default_button = button
                button.setDefault(True)
                button.setAutoDefault(True)
            else:
                button.setDefault(False)
                button.setAutoDefault(False)

        qconnect(self.rejected, partial(self._on_btn_clicked_or_dialog_rejected, None))

        return button_box

    def _on_btn_clicked_or_dialog_rejected(self, button_index: Optional[int]) -> None:
        # Prevent the callback from getting called recursively when it calls self.reject()
        if self._is_closing:
            return

        self._is_closing = True

        self.reject()

        if self.callback is not None:
            self.callback(button_index)

    def showEvent(self, event):
        """Set focus to the default button when the dialog is shown."""
        super().showEvent(event)

        if self.default_button:
            self.default_button.setFocus()


def show_dialog(
    text: str,
    title: str,
    parent: Optional[QWidget] = None,
    text_format: Qt.TextFormat = Qt.TextFormat.RichText,
    buttons: Optional[Sequence[ButtonParam]] = [QDialogButtonBox.StandardButton.Ok],
    default_button_idx: int = 0,
    scrollable: bool = False,
    callback: Optional[Callable[[int], None]] = None,
    icon: Optional[QIcon] = None,
    open_dialog: bool = True,
) -> _Dialog:
    """Show a dialog with the given text and buttons.
    The callback is called with the index of the clicked button."""
    if not parent:
        parent = aqt.mw.app.activeWindow() or aqt.mw

    dialog = _Dialog(
        parent=parent,
        text=text,
        title=title,
        text_format=text_format,
        buttons=buttons,
        default_button_idx=default_button_idx,
        scrollable=scrollable,
        callback=callback,
        icon=icon,
    )

    if open_dialog:
        dialog.open()

    return dialog


def tooltip_icon() -> QIcon:
    return QIcon(
        QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        )
    )


def warning_icon() -> QIcon:
    return QIcon(
        QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
    )


def tooltip_stylesheet() -> str:
    if theme_manager.night_mode:
        return """
            QToolTip { color: white; background-color: #2c2c2c; }
            """
    else:
        return """
            QToolTip { color: black; background-color: white; }
            """


def set_styled_tooltip(widget: QWidget, tooltip: str) -> None:
    widget.setToolTip(tooltip)

    # Add the tooltip style to the widget's stylesheet
    current_style_sheet = widget.styleSheet()
    new_style_sheet = f"{current_style_sheet} {tooltip_stylesheet()}"
    widget.setStyleSheet(new_style_sheet)


def check_and_prompt_for_updates_on_main_window():
    check_and_prompt_for_updates(
        parent=aqt.mw,
        mgr=aqt.mw.addonManager,
        on_done=aqt.mw.on_updates_installed,
        requested_by_user=True,
    )


def clear_layout(layout: QLayout) -> None:
    """Remove all widgets from a layout and delete them."""
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            widget = child.widget()
            widget.setParent(None)
            widget.deleteLater()
        elif child.layout():
            clear_layout(child.layout())


def extract_argument(
    func: Callable, args: Tuple, kwargs: Dict, arg_name: str
) -> Tuple[Tuple, Dict, Any]:
    """
    Extract and remove an argument from args or kwargs based on the function signature.

    Args:
    - func (callable): The function whose signature to follow.
    - args (tuple): The positional arguments.
    - kwargs (dict): The keyword arguments.
    - arg_name (str): The name of the argument to extract.

    Returns:
    - tuple: (new_args, new_kwargs, arg_value)
    """

    signature = inspect.signature(func)
    bound_args = signature.bind(*args, **kwargs)
    bound_args.apply_defaults()

    if arg_name not in bound_args.arguments:
        raise ValueError(f"Argument '{arg_name}' not found in the function signature.")

    arg_value = bound_args.arguments.pop(arg_name)

    # Reconstruct args and kwargs without the extracted argument
    new_args = []
    new_kwargs = {}
    for param in signature.parameters.values():
        if param.name in bound_args.arguments:
            if param.kind in [
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ]:
                new_args.append(bound_args.arguments[param.name])
            elif param.kind == inspect.Parameter.KEYWORD_ONLY:
                new_kwargs[param.name] = bound_args.arguments[param.name]

    return tuple(new_args), new_kwargs, arg_value
