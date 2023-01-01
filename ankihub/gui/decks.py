import uuid
from concurrent.futures import Future
from datetime import datetime
from typing import Callable, List, Optional
from uuid import UUID

from anki.collection import OpChanges
from aqt import dialogs, gui_hooks, mw
from aqt.browser import Browser
from aqt.emptycards import show_empty_cards
from aqt.operations.tag import clear_unused_tags
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    qconnect,
)
from aqt.studydeck import StudyDeck
from aqt.utils import openLink, showInfo, showText, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..addon_ankihub_client import AnkiHubRequestError
from ..ankihub_client import NoteInfo
from ..db import ankihub_db
from ..deck_hierarchy import (
    DECK_HIERARCHY_TAG_PREFIX,
    build_deck_hierarchy_and_move_cards_into_it,
    flatten_hierarchy,
)
from ..settings import URL_DECK_BASE, URL_DECKS, URL_HELP, URL_VIEW_DECK, config
from ..sync import AnkiHubImporter
from ..utils import create_backup, undo_note_type_modfications
from .utils import ask_user


class SubscribedDecksDialog(QDialog):
    _window: Optional["SubscribedDecksDialog"] = None
    silentlyClose = True

    def __init__(self):
        super(SubscribedDecksDialog, self).__init__()
        self.client = AnkiHubClient()
        self.setWindowTitle("Subscribed AnkiHub Decks")

        self._setup_ui()
        self._on_item_selection_changed()
        self._refresh_decks_list()

        if not self.client.has_token():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def _setup_ui(self):
        self.box_top = QVBoxLayout()
        self.box_above = QHBoxLayout()
        self.box_right = QVBoxLayout()

        self.decks_list = QListWidget()
        qconnect(self.decks_list.itemSelectionChanged, self._on_item_selection_changed)

        self.add_btn = QPushButton("Add")
        self.box_right.addWidget(self.add_btn)
        qconnect(self.add_btn.clicked, self._on_add)

        self.unsubscribe_btn = QPushButton("Unsubscribe")
        self.box_right.addWidget(self.unsubscribe_btn)
        qconnect(self.unsubscribe_btn.clicked, self._on_unsubscribe)

        self.open_web_btn = QPushButton("Open on AnkiHub")
        self.box_right.addWidget(self.open_web_btn)
        qconnect(self.open_web_btn.clicked, self._on_open_web)

        self.set_home_deck_btn = QPushButton("Set Home deck")
        self.set_home_deck_btn.setToolTip("New cards will be added to this deck.")
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self.box_right.addWidget(self.set_home_deck_btn)

        self.toggle_subdecks_btn = QPushButton("Enable Subdecks")
        self.toggle_subdecks_btn.setToolTip(
            "Toggle between deck being organized as subdecks or not."
        )
        qconnect(self.toggle_subdecks_btn.clicked, self._on_toggle_subdecks)
        self.box_right.addWidget(self.toggle_subdecks_btn)

        self.box_right.addStretch(1)

        self.setLayout(self.box_top)
        self.box_top.addLayout(self.box_above)
        self.box_above.addWidget(self.decks_list)
        self.box_above.addLayout(self.box_right)

    def _refresh_decks_list(self) -> None:
        self.decks_list.clear()
        for ah_did in config.deck_ids():
            name = config.deck_config(ah_did).name
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, ah_did)
            self.decks_list.addItem(item)

    def _refresh_anki(self) -> None:
        op = OpChanges()
        op.deck = True
        op.browser_table = True
        op.browser_sidebar = True
        op.study_queues = True
        gui_hooks.operation_did_execute(op, handler=None)

    def _on_add(self) -> None:
        ah_did = SubscribeDialog().run()
        if ah_did is None:
            return

        self._refresh_decks_list()
        self._refresh_anki()

        anki_did = config.deck_config(ah_did).anki_id
        deck_name = mw.col.decks.name(anki_did)
        if mw.col.find_notes(f'"deck:{deck_name}" "tag:{DECK_HIERARCHY_TAG_PREFIX}*"'):
            if ask_user(
                "The deck you subscribed to contains subdeck tags.<br>"
                "Do you want to enable subdecks for this deck?"
            ):
                self._select_deck(ah_did)
                self._on_toggle_subdecks()

        cleanup_after_deck_install()

    def _select_deck(self, ah_did: uuid.UUID):
        deck_item = next(
            (
                item
                for i in range(self.decks_list.count())
                if (item := self.decks_list.item(i)).data(Qt.ItemDataRole.UserRole)
                == ah_did
            ),
            None,
        )
        if deck_item is not None:
            self.decks_list.setCurrentItem(deck_item)

    def _on_unsubscribe(self) -> None:
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return
        deck_names = [item.text() for item in items]
        deck_names_text = ", ".join(deck_names)
        confirm = ask_user(
            f"Unsubscribe from deck {deck_names_text}?\n\n"
            "The deck will remain in your collection, but it will no longer sync with AnkiHub.",
            title="Unsubscribe AnkiHub Deck",
        )
        if not confirm:
            return

        for item in items:
            ankihub_did: UUID = item.data(Qt.ItemDataRole.UserRole)
            config.unsubscribe_deck(ankihub_did)
            self.unsubscribe_from_deck(ankihub_did)

        tooltip("Unsubscribed from AnkiHub Deck.", parent=mw)
        self._refresh_decks_list()

    @staticmethod
    def unsubscribe_from_deck(ankihub_did: UUID) -> None:
        mids = ankihub_db.note_types_for_ankihub_deck(ankihub_did)
        undo_note_type_modfications(mids)
        ankihub_db.remove_deck(ankihub_did)

    def _on_open_web(self) -> None:
        items = self.decks_list.selectedItems()
        if len(items) == 0:
            return

        for item in items:
            ankihub_id: UUID = item.data(Qt.ItemDataRole.UserRole)
            openLink(f"{URL_DECK_BASE}/{ankihub_id}")

    def _on_set_home_deck(self):
        deck_names = self.decks_list.selectedItems()
        if len(deck_names) == 0:
            return

        deck_name = deck_names[0]
        ankihub_id: UUID = deck_name.data(Qt.ItemDataRole.UserRole)
        current_home_deck = mw.col.decks.get(config.deck_config(ankihub_id).anki_id)
        if current_home_deck is None:
            current = None
        else:
            current = current_home_deck["name"]

        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=ankihub_id, anki_did=anki_did)
            tooltip("Home deck updated.", parent=self)

        # this lets the user pick a deck
        StudyDeckWithoutHelpButton(
            mw,
            current=current,
            accept="Set Home Deck",
            title="Change Home Deck",
            parent=self,
            callback=update_deck_config,
        )

    def _on_toggle_subdecks(self):
        deck_names = self.decks_list.selectedItems()
        if len(deck_names) == 0:
            return

        deck_name = deck_names[0]
        ankihub_id: UUID = deck_name.data(Qt.ItemDataRole.UserRole)
        using_subdecks = config.deck_config(ankihub_id).subdecks_enabled

        def on_done(future: Future):
            future.result()

            tooltip("Subdecks updated.", parent=self)
            mw.deckBrowser.refresh()
            browser: Optional[Browser] = dialogs._dialogs["Browser"][1]
            if browser is not None:
                browser.sidebar.refresh()

        if using_subdecks:
            flatten = ask_user("Do you want to remove the subdecks?")
            if flatten is None:
                return
            elif flatten:
                mw.taskman.with_progress(
                    label="Flattening into single deck",
                    task=lambda: flatten_hierarchy(ankihub_id),
                    on_done=on_done,
                )
        else:
            mw.taskman.with_progress(
                label="Moving cards into subdecks",
                task=lambda: build_deck_hierarchy_and_move_cards_into_it(ankihub_id),
                on_done=on_done,
            )

        config.set_subdecks(ankihub_id, not using_subdecks)

        self._refresh_subdecks_button()

    def _refresh_subdecks_button(self):
        selection = self.decks_list.selectedItems()
        one_selected: bool = len(selection) == 1

        self.toggle_subdecks_btn.setEnabled(one_selected)
        if not one_selected:
            return

        ankihub_did: UUID = selection[0].data(Qt.ItemDataRole.UserRole)
        using_subdecks = config.deck_config(ankihub_did).subdecks_enabled
        self.toggle_subdecks_btn.setText(
            "Disable Subdecks" if using_subdecks else "Enable Subdecks"
        )

    def _on_item_selection_changed(self) -> None:
        selection = self.decks_list.selectedItems()
        one_selected: bool = len(selection) == 1

        self.unsubscribe_btn.setEnabled(one_selected)
        self.open_web_btn.setEnabled(one_selected)
        self.set_home_deck_btn.setEnabled(one_selected)

        self._refresh_subdecks_button()

    @classmethod
    def display_subscribe_window(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window._refresh_decks_list()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()
        return cls._window


class StudyDeckWithoutHelpButton(StudyDeck):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.form.buttonBox.removeButton(
            self.form.buttonBox.button(QDialogButtonBox.StandardButton.Help)
        )


class SubscribeDialog(QDialog):
    silentlyClose = True

    def __init__(self):
        super(SubscribeDialog, self).__init__()

        self.ah_did = None

        self.results = None
        self.thread = None  # type: ignore
        self.box_top = QVBoxLayout()
        self.box_mid = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()

        self.deck_id_box = QHBoxLayout()
        self.deck_id_box_label = QLabel("Deck ID:")
        self.deck_id_box_text = QLineEdit("", self)
        self.deck_id_box_text.setMinimumWidth(300)
        self.deck_id_box.addWidget(self.deck_id_box_label)
        self.deck_id_box.addWidget(self.deck_id_box_text)
        self.box_left.addLayout(self.deck_id_box)

        self.box_mid.addLayout(self.box_left)
        self.box_mid.addSpacing(20)
        self.box_mid.addLayout(self.box_right)

        self.buttonbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel  # type: ignore
        )
        self.buttonbox.button(QDialogButtonBox.StandardButton.Ok).setText("Subscribe")
        self.browse_btn = self.buttonbox.addButton(
            "Browse Decks", QDialogButtonBox.ButtonRole.ActionRole
        )
        qconnect(self.browse_btn.clicked, self._on_browse_deck)
        qconnect(self.buttonbox.accepted, self._subscribe)
        self.buttonbox.rejected.connect(self.close)

        self.instructions_label = QLabel(
            "<center>Copy/Paste a Deck ID from AnkiHub.net/decks to subscribe.</center>"
        )
        # Add all widgets to top layout.
        self.box_top.addWidget(self.instructions_label)
        self.box_top.addSpacing(10)
        self.box_top.addLayout(self.box_mid)
        self.box_top.addStretch(1)
        self.box_top.addWidget(self.buttonbox)
        self.setLayout(self.box_top)

        self.setMinimumWidth(500)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Subscribe to AnkiHub Deck")

        self.client = AnkiHubClient()
        if not self.client.has_token():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def run(self) -> uuid.UUID:
        """Returns the ankihub deck id of the newly subscribed deck or None if no deck was subscribed to"""
        self.exec()
        return self.ah_did

    def _subscribe(self):
        ah_did_str = self.deck_id_box_text.text().strip()

        try:
            ah_did = uuid.UUID(ah_did_str)
        except ValueError:
            showInfo(
                "The format of the Deck ID is invalid. Please make sure you copied the Deck ID correctly."
            )
            return

        if ah_did in config.deck_ids():
            showText(
                f"You've already subscribed to deck {ah_did}. "
                "Syncing with AnkiHub will happen automatically everytime you "
                "restart Anki. You can manually sync with AnkiHub from the AnkiHub "
                f"menu. See {URL_HELP} for more details."
            )
            self.close()
            return self.ah_did

        confirmed = ask_user(
            f"Would you like to proceed with downloading and installing the deck? "
            f"Your personal collection will be modified.<br><br>"
            f"See <a href='{URL_HELP}'>{URL_HELP}</a> for details.",
            title="Please confirm to proceed.",
        )
        if not confirmed:
            return

        def on_success():
            self.ah_did = ah_did
            self.accept()

        download_and_install_deck(ah_did, on_success=on_success, on_failure=self.reject)

    def _on_browse_deck(self) -> None:
        openLink(URL_DECKS)


def download_and_install_deck(
    ankihub_did: uuid.UUID,
    on_success: Optional[Callable[[], None]] = None,
    on_failure: Optional[Callable[[], None]] = None,
):
    def on_install_done(future: Future):
        success = False
        exc = None
        try:
            success = future.result()
        except Exception as e:
            exc = e

        if exc is not None or not success:
            LOGGER.debug("Error installing deck.")
            if on_failure is not None:
                on_failure()

            if exc:
                raise exc
        else:
            mw.reset()

            if on_success is not None:
                on_success()

    try:
        deck_info = AnkiHubClient().get_deck_by_id(ankihub_did)
    except AnkiHubRequestError as e:
        if e.response.status_code == 404:
            showText(
                f"Deck {ankihub_did} doesn't exist. Please make sure to copy/paste "
                f"the correct ID. If you believe this is an error, please reach "
                f"out to user support at help@ankipalace.com."
            )
            return
        elif e.response.status_code == 403:
            url_view_deck = f"{URL_VIEW_DECK}{ankihub_did}"
            showInfo(
                f"Please first subscribe to the deck on the AnkiHub website.<br><br>"
                f'Link to the deck: <a href="{url_view_deck}">{url_view_deck}</a>',
            )
            return
        else:
            raise e

    def on_download_done(future: Future) -> None:
        notes_data: List[NoteInfo] = future.result()

        mw.taskman.with_progress(
            lambda: install_deck(
                notes_data=notes_data,
                deck_name=deck_info.name,
                ankihub_did=ankihub_did,
                latest_update=deck_info.csv_last_upload,
                is_creator=deck_info.owner,
            ),
            on_done=on_install_done,
            parent=mw,
            label="Installing deck",
        )

    mw.taskman.with_progress(
        lambda: AnkiHubClient().download_deck(
            deck_info.ankihub_deck_uuid, download_progress_cb=download_progress_cb
        ),
        on_done=on_download_done,
        parent=mw,
        label="Downloading deck...",
    )


def install_deck(
    notes_data: List[NoteInfo],
    deck_name: str,
    ankihub_did: UUID,
    latest_update: datetime,
    is_creator: bool,
) -> bool:
    """If we have a .csv, read data from the file and modify the user's note types
    and notes.
    :param: path to the .csv or .apkg file
    :return: True if successful, False if not
    """

    create_backup()

    importer = AnkiHubImporter()
    local_did = importer.import_ankihub_deck(
        ankihub_did=ankihub_did,
        notes_data=notes_data,
        deck_name=deck_name,
    )

    config.save_subscription(
        name=deck_name,
        ankihub_did=ankihub_did,
        anki_did=local_did,
        latest_udpate=latest_update,
        creator=is_creator,
    )

    LOGGER.debug("Importing deck was succesful.")

    return True


def download_progress_cb(percent: int):
    # adding +1 to avoid progress increasing while at 0% progress
    # (the mw.progress.update function does that)
    mw.taskman.run_on_main(
        lambda: mw.progress.update(
            label="Downloading deck...",
            value=percent + 1,
            max=101,
        )
    )


def cleanup_after_deck_install(multiple_decks: bool = False) -> None:
    message = (
        (
            "The deck has been successfully installed!<br><br>"
            if not multiple_decks
            else ""
        )
        + "Do you want to clear unused tags and empty cards from your collection? (It is recommended.)"
    )
    if ask_user(message, title="AnkiHub"):
        clear_unused_tags(parent=mw).run_in_background()
        show_empty_cards(mw)
