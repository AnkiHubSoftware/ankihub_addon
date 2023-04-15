"""Dialog for managing subscriptions to AnkiHub decks and deck-specific settings."""
import uuid
from concurrent.futures import Future
from datetime import datetime
from typing import Callable, List, Optional
from uuid import UUID

import aqt
from anki.collection import OpChanges
from aqt import dialogs, gui_hooks
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
from ..importing import AnkiHubImportResult
from ..media_sync import media_sync
from ..messages import messages
from ..settings import config, url_deck_base, url_decks, url_help, url_view_deck
from ..subdecks import SUBDECK_TAG, build_subdecks_and_move_cards_to_them, flatten_deck
from ..sync import AnkiHubImporter
from ..utils import create_backup, undo_note_type_modfications
from .utils import ask_user, set_tooltip_icon


def cleanup_after_deck_install(multiple_decks: bool = False) -> None:
    message = (
        (
            "The deck has been successfully installed!<br><br>"
            if not multiple_decks
            else ""
        )
        + "Do you want to clear unused tags and empty cards from your collection? (recommended)"
    )
    if ask_user(message, title="AnkiHub"):
        clear_unused_tags(parent=aqt.mw).run_in_background()
        show_empty_cards(aqt.mw)


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

        if not config.is_logged_in():
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
        set_tooltip_icon(self.set_home_deck_btn)
        qconnect(self.set_home_deck_btn.clicked, self._on_set_home_deck)
        self.box_right.addWidget(self.set_home_deck_btn)

        self.toggle_subdecks_btn = QPushButton("Enable Subdecks")
        self.toggle_subdecks_btn.setToolTip(
            "Toggle between the deck being organized into subdecks or not.<br>"
            f"This will only have an effect if notes in the deck have <b>{SUBDECK_TAG}</b> tags."
        )
        set_tooltip_icon(self.toggle_subdecks_btn)
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
        import_result = SubscribeDialog().run()
        if import_result is None:
            return

        ah_did = import_result.ankihub_did

        self._refresh_decks_list()
        self._refresh_anki()

        anki_did = config.deck_config(ah_did).anki_id
        deck_name = aqt.mw.col.decks.name(anki_did)
        if aqt.mw.col.find_notes(f'"deck:{deck_name}" "tag:{SUBDECK_TAG}*"'):
            if ask_user(
                "The deck you subscribed to contains subdeck tags.<br>"
                "Do you want to enable subdecks for this deck?"
            ):
                self._select_deck(ah_did)
                self._on_toggle_subdecks()

        cleanup_after_deck_install()

        showInfo(
            text=messages.deck_import_summary(deck_name, import_result),
            parent=self,
            title="AnkiHub Deck Import Summary",
        )

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

        tooltip("Unsubscribed from AnkiHub Deck.", parent=aqt.mw)
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
            openLink(f"{url_deck_base()}/{ankihub_id}")

    def _on_set_home_deck(self):
        deck_names = self.decks_list.selectedItems()
        if len(deck_names) == 0:
            return

        deck_name = deck_names[0]
        ankihub_id: UUID = deck_name.data(Qt.ItemDataRole.UserRole)
        current_home_deck = aqt.mw.col.decks.get(config.deck_config(ankihub_id).anki_id)
        if current_home_deck is None:
            current = None
        else:
            current = current_home_deck["name"]

        def update_deck_config(ret: StudyDeck):
            if not ret.name:
                return

            anki_did = aqt.mw.col.decks.id(ret.name)
            config.set_home_deck(ankihub_did=ankihub_id, anki_did=anki_did)
            tooltip("Home deck updated.", parent=self)

        # this lets the user pick a deck
        StudyDeckWithoutHelpButton(
            aqt.mw,
            current=current,
            accept="Set Home Deck",
            title="Change Home Deck",
            parent=self,
            callback=update_deck_config,
        )

    def _on_toggle_subdecks(self):
        deck_items = self.decks_list.selectedItems()
        if len(deck_items) == 0:
            return

        deck_item = deck_items[0]
        ankihub_id: UUID = deck_item.data(Qt.ItemDataRole.UserRole)
        deck_config = config.deck_config(ankihub_id)
        using_subdecks = deck_config.subdecks_enabled

        if aqt.mw.col.decks.name_if_exists(deck_config.anki_id) is None:
            showInfo(
                (
                    f"Anki deck <b>{deck_config.name}</b> doesn't exist in your Anki collection.<br>"
                    "It might help to reset local changes to the deck first.<br>"
                    "(You can do that from the AnkiHub menu in the Anki browser.)"
                ),
                parent=self,
            )
            return

        def on_done(future: Future):
            future.result()

            tooltip("Subdecks updated.", parent=self)
            aqt.mw.deckBrowser.refresh()
            browser: Optional[Browser] = dialogs._dialogs["Browser"][1]
            if browser is not None:
                browser.sidebar.refresh()

        if using_subdecks:
            flatten = ask_user(
                "Do you want to remove the subdecks of<br>"
                f"<i>{deck_item.text()}</i>?<br><br>"
                "<b>Warning:</b> This will remove all subdecks of this deck and move "
                "all of its cards back to the main deck.</b>",
                defaultno=True,
            )
            if flatten is None:
                return
            elif flatten:
                aqt.mw.taskman.with_progress(
                    label="Removing subdecks and moving cards...",
                    task=lambda: flatten_deck(ankihub_id),
                    on_done=on_done,
                )
        else:
            aqt.mw.taskman.with_progress(
                label="Building subdecks and moving cards...",
                task=lambda: build_subdecks_and_move_cards_to_them(ankihub_id),
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

        self.import_result: Optional[AnkiHubImportResult] = None

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
        if not config.is_logged_in():
            showText("Oops! Please make sure you are logged into AnkiHub!")
            self.close()
        else:
            self.show()

    def run(self) -> Optional[AnkiHubImportResult]:
        self.exec()
        return self.import_result

    def _subscribe(self) -> None:
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
                f"menu. See {url_help()} for more details."
            )
            self.close()
            return

        confirmed = ask_user(
            f"Would you like to proceed with downloading and installing the deck? "
            f"Your personal collection will be modified.<br><br>"
            f"See <a href='{url_help()}'>{url_help()}</a> for details.",
            title="Please confirm to proceed.",
        )
        if not confirmed:
            return

        def on_success(import_result: AnkiHubImportResult):
            self.import_result = import_result
            self.accept()

        download_and_install_deck(ah_did, on_success=on_success, on_failure=self.reject)

    def _on_browse_deck(self) -> None:
        openLink(url_decks())


def download_and_install_deck(
    ankihub_did: uuid.UUID,
    on_success: Optional[Callable[[AnkiHubImportResult], None]] = None,
    on_failure: Optional[Callable[[], None]] = None,
):
    def on_install_done(future: Future):
        try:
            import_result: AnkiHubImportResult = future.result()
        except Exception as exc:
            LOGGER.info("Error installing deck.")
            if on_failure is not None:
                on_failure()

            raise exc

        aqt.mw.reset()

        if on_success is not None:
            on_success(import_result)

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
            deck_url = f"{url_view_deck()}{ankihub_did}"
            showInfo(
                f"Please first subscribe to the deck on the AnkiHub website.<br>"
                f"Link to the deck: <a href='{deck_url}'>{deck_url}</a><br>"
                "<br>"
                "Note that you also need an active AnkiHub subscription.<br>"
                "You can get a subscription at<br>"
                "<a href='https://www.ankihub.net/'>https://www.ankihub.net/</a>",
            )
            return
        else:
            raise e

    def on_download_done(future: Future) -> None:
        notes_data: List[NoteInfo] = future.result()

        aqt.mw.taskman.with_progress(
            lambda: _install_deck(
                notes_data=notes_data,
                deck_name=deck_info.name,
                ankihub_did=ankihub_did,
                latest_update=deck_info.csv_last_upload,
                is_creator=deck_info.owner,
            ),
            on_done=on_install_done,
            parent=aqt.mw,
            label="Installing deck...",
        )

    aqt.mw.taskman.with_progress(
        lambda: AnkiHubClient().download_deck(
            deck_info.ankihub_deck_uuid, download_progress_cb=_download_progress_cb
        ),
        on_done=on_download_done,
        parent=aqt.mw,
        label="Downloading deck...",
    )


def _install_deck(
    notes_data: List[NoteInfo],
    deck_name: str,
    ankihub_did: UUID,
    latest_update: datetime,
    is_creator: bool,
) -> AnkiHubImportResult:
    """Imports the notes_data into the Anki collection.
    Saves the deck subscription to the config file.
    Starts the media download.
    Returns information about the import.
    """
    create_backup()

    importer = AnkiHubImporter()
    import_result = importer.import_ankihub_deck(
        ankihub_did=ankihub_did,
        notes_data=notes_data,
        deck_name=deck_name,
    )

    config.save_subscription(
        name=deck_name,
        ankihub_did=ankihub_did,
        anki_did=import_result.anki_did,
        latest_udpate=latest_update,
        creator=is_creator,
    )

    media_sync.start_media_download()

    LOGGER.info("Importing deck was succesful.")

    return import_result


def _download_progress_cb(percent: int):
    # adding +1 to avoid progress increasing while at 0% progress
    # (the aqt.mw.progress.update function does that)
    aqt.mw.taskman.run_on_main(
        lambda: aqt.mw.progress.update(
            label="Downloading deck...",
            value=percent + 1,
            max=101,
        )
    )
