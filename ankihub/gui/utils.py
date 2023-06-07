import uuid
from typing import Any, List, Optional

import aqt
from aqt.addons import check_and_prompt_for_updates
from aqt.qt import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QIcon,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    Qt,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import disable_help_button, showWarning, tooltip

from ..settings import config


def show_error_dialog(message, *args, **kwargs):
    aqt.mw.taskman.run_on_main(lambda: showWarning(message, *args, **kwargs))  # type: ignore


def choose_subset(
    prompt: str,
    choices: List[str],
    current: List[str] = [],
    adjust_height_to_content=True,
    description_html: Optional[str] = None,
    parent: Any = None,
) -> List[str]:
    if not parent:
        parent = aqt.mw.app.activeWindow()

    dialog = QDialog(parent)
    disable_help_button(dialog)

    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    layout = QVBoxLayout()
    dialog.setLayout(layout)
    label = QLabel(prompt)
    layout.addWidget(label)
    list_widget = QListWidget()
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

    # toggle item check state when clicked
    qconnect(
        list_widget.itemClicked,
        lambda item: item.setCheckState(
            Qt.CheckState.Checked
            if item.checkState() == Qt.CheckState.Unchecked
            else Qt.CheckState.Unchecked
        ),
    )

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

    dialog.exec()

    result = [
        list_widget.item(i).text()
        for i in range(list_widget.count())
        if list_widget.item(i).checkState() == Qt.CheckState.Checked
    ]
    return result


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
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
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
    show_cancel_button: bool = True,
    yes_button_label: str = "Yes",
    no_button_label: str = "No",
) -> Optional[bool]:
    "Show a yes/no question. Return true if yes. Return false if no. Return None if cancelled."

    if not parent:
        parent = aqt.mw.app.activeWindow()

    msg = QMessageBox(parent=parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    msg.setTextFormat(Qt.TextFormat.RichText)

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


def show_tooltip(
    msg: str,
    period: int = 3000,
    parent: Optional[QWidget] = None,
):
    """Safer version of tooltip that doesn't cause an error if the parent widget is deleted and
    instead shows the tooltip on the active or main window."""
    try:
        tooltip(msg, period, parent)
    except RuntimeError as e:
        if "wrapped C/C++ object of type" in str(e) and "has been deleted" in str(e):
            tooltip(msg, period)
        else:
            raise e


def set_tooltip_icon(btn: QPushButton) -> None:
    btn.setIcon(
        QIcon(
            QApplication.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxInformation
            )
        )
    )


def check_and_prompt_for_updates_on_main_window():
    check_and_prompt_for_updates(
        parent=aqt.mw,
        mgr=aqt.mw.addonManager,
        on_done=aqt.mw.on_updates_installed,
        requested_by_user=True,
    )
