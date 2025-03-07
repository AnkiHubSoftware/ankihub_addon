"""AnkiHub menu on Anki's main window."""

import re
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aqt
from aqt import (
    AnkiApp,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from aqt.qt import QAction, QDialog, QKeySequence, QMenu, Qt, qconnect
from aqt.utils import openLink, showInfo, tooltip

from .. import LOGGER
from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import AnkiHubHTTPError
from ..db import ankihub_db
from ..media_import.ui import open_import_dialog
from ..settings import ADDON_VERSION, config
from .config_dialog import get_config_dialog_manager
from .decks_dialog import DeckManagementDialog
from .errors import upload_logs_and_data_in_background, upload_logs_in_background
from .media_sync import media_sync
from .operations.ankihub_sync import sync_with_ankihub
from .operations.deck_creation import create_collaborative_deck
from .utils import (
    ask_user,
    check_and_prompt_for_updates_on_main_window,
    choose_ankihub_deck,
)


@dataclass
class _MenuState:
    ankihub_menu: Optional[QMenu] = None


menu_state = _MenuState()


def setup_ankihub_menu() -> None:
    menu_state.ankihub_menu = QMenu("&AnkiHub", parent=aqt.mw)
    # We can't leave the menu empty, otherwise it won't show up on MacOS
    menu_state.ankihub_menu.addAction("Loading...").setEnabled(False)
    aqt.mw.form.menubar.addMenu(menu_state.ankihub_menu)

    qconnect(menu_state.ankihub_menu.aboutToShow, refresh_ankihub_menu)


def refresh_ankihub_menu() -> None:
    """Add top-level AnkiHub menu."""
    menu_state.ankihub_menu.clear()

    if config.is_logged_in():
        _create_collaborative_deck_setup(parent=menu_state.ankihub_menu)
        _subscribed_decks_setup(parent=menu_state.ankihub_menu)
        _sync_with_ankihub_setup(parent=menu_state.ankihub_menu)
        _media_sync_status_setup(parent=menu_state.ankihub_menu)
        _import_media_setup(parent=menu_state.ankihub_menu)
        _upload_deck_media_setup(parent=menu_state.ankihub_menu)
        _ankihub_logout_setup(parent=menu_state.ankihub_menu)
    else:
        _ankihub_login_setup(parent=menu_state.ankihub_menu)

    _config_setup(parent=menu_state.ankihub_menu)
    _ankihub_terms_and_policy_setup(parent=menu_state.ankihub_menu)
    _ankihub_help_setup(parent=menu_state.ankihub_menu)


class AnkiHubLogin(QWidget):
    _window: Optional["AnkiHubLogin"] = None
    silentlyClose = True

    def __init__(self):
        super(AnkiHubLogin, self).__init__()
        self.results = None
        self.thread = None  # type: ignore
        self.box_top = QVBoxLayout()
        self.box_upper = QHBoxLayout()
        self.box_left = QVBoxLayout()
        self.box_right = QVBoxLayout()
        self.bottom_box_section = QHBoxLayout()

        # Username
        self.username_or_email_box = QHBoxLayout()
        self.username_or_email_box_label = QLabel("Username or E-mail:")
        self.username_or_email_box_text = QLineEdit("", self)
        self.username_or_email_box_text.setMinimumWidth(300)
        self.username_or_email_box.addWidget(self.username_or_email_box_label)
        self.username_or_email_box.addWidget(self.username_or_email_box_text)
        self.box_left.addLayout(self.username_or_email_box)

        # Password
        self.password_box = QHBoxLayout()

        self.password_box_label = QLabel("Password:")

        self.password_box_text = QLineEdit("", self)
        self.password_box_text.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_box_text.setMinimumWidth(300)
        qconnect(self.password_box_text.returnPressed, self.login)

        self.toggle_button = QPushButton("Show")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setFixedHeight(30)
        qconnect(self.toggle_button.toggled, self.refresh_password_visibility)

        self.password_box.addWidget(self.password_box_label)
        self.password_box.addWidget(self.password_box_text)
        self.password_box.addWidget(self.toggle_button)
        self.box_left.addLayout(self.password_box)

        # Sign in button
        self.login_button = QPushButton("Sign in", self)
        self.bottom_box_section.addWidget(self.login_button)
        qconnect(self.login_button.clicked, self.login)
        self.login_button.setDefault(True)
        self.bottom_box_section.setContentsMargins(0, 12, 0, 12)
        self.box_left.addLayout(self.bottom_box_section)

        # Sign up / forgot password text
        self.sign_up_and_recover_password_container = QVBoxLayout()
        self.sign_up_and_recover_password_container.setSpacing(8)
        self.sign_up_and_recover_password_container.setContentsMargins(0, 0, 0, 5)
        self.login_button.setDefault(True)
        self.sign_up_help_text = QLabel(
            'Don\'t have an AnkiHub account? <a href="https://app.ankihub.net/accounts/signup/">Register now</a>'
        )
        self.sign_up_help_text.setOpenExternalLinks(True)
        self.recover_password_help_text = QLabel(
            '<a href="https://app.ankihub.net/accounts/password/reset/">Forgot password?</a>'
        )
        self.recover_password_help_text.setOpenExternalLinks(True)
        self.sign_up_and_recover_password_container.addWidget(self.sign_up_help_text)
        self.sign_up_and_recover_password_container.addWidget(
            self.recover_password_help_text
        )
        self.box_left.addLayout(self.sign_up_and_recover_password_container)

        # Add left and right layouts to upper
        self.box_upper.addLayout(self.box_left)
        self.box_upper.addSpacing(20)
        self.box_upper.addLayout(self.box_right)

        # Add all widgets to top layout.
        self.box_top.addLayout(self.box_upper)
        self.box_top.addStretch(1)
        self.setLayout(self.box_top)

        self.setContentsMargins(20, 5, 0, 5)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        self.setWindowTitle("Sign in to AnkiHub.")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)  # type: ignore
        self.show()

    def refresh_password_visibility(self) -> None:
        if self.toggle_button.isChecked():
            self.password_box_text.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_button.setText("Hide")
        else:
            self.password_box_text.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_button.setText("Show")

    def login(self):
        username_or_email = self.username_or_email_box_text.text()
        password = self.password_box_text.text()
        if not all([username_or_email, password]):
            showInfo("Oops! You forgot to put in a username or password!")
            return
        ankihub_client = AnkiHubClient()

        try:
            credentials = {"password": password}
            if self._is_email(username_or_email):
                credentials.update({"email": username_or_email})
            else:
                credentials.update({"username": username_or_email})
            token = ankihub_client.login(credentials=credentials)
        except AnkiHubHTTPError as e:
            LOGGER.info("AnkiHub login failed.")
            config.save_token("")

            if e.response.status_code == 400:
                tooltip("Wrong credentials.", parent=aqt.mw)
                return

            raise e

        config.save_token(token)
        config.save_user_email(username_or_email)
        username = ""
        if not self._is_email(username_or_email):
            username = username_or_email
        config.save_username(username)
        LOGGER.info("User signed into AnkiHub.", user=username_or_email)

        tooltip("Signed into AnkiHub!", parent=aqt.mw)
        self.close()

    def _is_email(self, value):
        return re.fullmatch(
            r"^[a-zA-Z0-9.!#$%&’*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
            value,
        )

    def clear_fields(self):
        self.username_or_email_box_text.setText("")
        self.password_box_text.setText("")

    @classmethod
    def display_login(cls):
        if cls._window is None:
            cls._window = cls()
        else:
            cls._window.clear_fields()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()

        LOGGER.info("Showed AnkiHub login dialog.")
        return cls._window


def _create_collaborative_deck_setup(parent: QMenu):
    q_action = QAction("🛠️ Create AnkiHub Deck", parent=parent)
    qconnect(q_action.triggered, create_collaborative_deck)
    parent.addAction(q_action)


def _confirm_sign_out():
    confirm = ask_user(
        "Are you sure you want to Sign out?",
        yes_button_label="Sign Out",
        no_button_label="Cancel",
    )
    if not confirm:
        return

    _sign_out_action()
    LOGGER.info("User signed out of AnkiHub.")


def _sign_out_action():
    try:
        AnkiHubClient().signout()
    finally:
        config.save_token("")
        tooltip("Signed out of AnkiHub!", parent=aqt.mw)


class LogUploadResultDialog(QDialog):
    def __init__(self, log_file_name: str):
        super().__init__(parent=aqt.mw)

        self.setWindowTitle("AnkiHub")

        self.layout_ = QVBoxLayout()
        self.setLayout(self.layout_)

        self.label = QLabel(
            "Logs uploaded successfully!<br><br>"
            " Please copy this file name and include it in your bug report:<br><br>"
            f"<b>{log_file_name}</b>",
        )
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.layout_.addWidget(self.label)

        self.layout_.addSpacing(8)

        def on_click() -> None:
            AnkiApp.clipboard().setText(log_file_name)

        self.button = QPushButton("Copy to clipboard")
        self.button.clicked.connect(on_click)  # type: ignore
        self.layout_.addWidget(self.button)


def _upload_logs_action() -> None:
    if not ask_user(
        "Do you want to upload the add-on's logs to AnkiHub to go along a bug report?"
    ):
        return

    aqt.mw.progress.start(label="Uploading logs...", parent=aqt.mw, immediate=True)
    upload_logs_in_background(on_done=_on_logs_uploaded, hide_username=True)


def _upload_logs_and_data_action() -> None:
    if not ask_user(
        "Do you want to upload the add-on's logs and data to AnkiHub to go along a bug report?<br><br>"
        "This can take a while."
    ):
        return

    aqt.mw.progress.start(
        label="Uploading logs and data...", parent=aqt.mw, immediate=True
    )
    upload_logs_and_data_in_background(on_done=_on_logs_uploaded)


def _on_logs_uploaded(log_file_name: str) -> None:
    aqt.mw.progress.finish()
    LogUploadResultDialog(log_file_name=log_file_name).exec()


def _ankihub_login_setup(parent: QMenu) -> None:
    sign_in_button = QAction("🔑 Sign into AnkiHub", aqt.mw)
    qconnect(sign_in_button.triggered, AnkiHubLogin.display_login)
    parent.addAction(sign_in_button)


def _subscribed_decks_setup(parent: QMenu):
    q_action = QAction("📚 Deck Management", aqt.mw)
    qconnect(q_action.triggered, DeckManagementDialog.display_subscribe_window)
    parent.addAction(q_action)


def _import_media_setup(parent: QMenu):
    q_action = QAction("🖼️ Import media", aqt.mw)
    qconnect(q_action.triggered, open_import_dialog)
    parent.addAction(q_action)


def _sync_with_ankihub_setup(parent: QMenu):
    """Set up the menu item for uploading suggestions in bulk."""
    q_action = QAction("🔃️ Sync with AnkiHub", aqt.mw)

    def on_done(future: Future):
        future.result()

    qconnect(q_action.triggered, lambda: sync_with_ankihub(on_done=on_done))
    if sync_hotkey := config.public_config["sync_hotkey"]:
        try:
            q_action.setShortcut(QKeySequence(sync_hotkey))
        except Exception:
            LOGGER.exception("Failed to set sync hotkey.", sync_hotkey=sync_hotkey)
    parent.addAction(q_action)


def _upload_deck_media_setup(parent: QMenu):
    """Set up the menu item for manually triggering the upload
    of all the media files for a given deck (logged user MUST be the
    deck owner)"""

    q_action = QAction("📸 Upload media for deck", aqt.mw)
    qconnect(q_action.triggered, _upload_deck_media_action)
    parent.addAction(q_action)


def _upload_deck_media_action() -> None:
    LOGGER.info("User clicked on 'Upload media for deck' menu item.")

    client = AnkiHubClient()

    # Fetch the ankihub deck ids of all decks the user owns
    owned_ah_dids = client.owned_deck_ids()

    # If the user has no owned decks, we should show a message informing them
    # about this and not allow them to upload media.
    if not owned_ah_dids:
        showInfo(
            "<b>Oh no!</b> 🙁<br>"
            "You do not own any AnkiHub decks. You can only upload media for decks that you own.<br><br>"
            "Maybe try creating a new AnkiHub deck for yourself, or create a note suggestion instead? 🙂"
        )
        return

    # The user owns one or more Decks but they are not installed locally
    if owned_ah_dids and not any(
        [did for did in owned_ah_dids if did in config.deck_ids()]
    ):
        showInfo(
            "<b>Oh no!</b> 🙁<br>"
            "It seems that you have deck(s) that you own at AnkiHub, but none of them are installed locally.<br><br>"
            "Plase subscribe to the deck from the add-on before trying to upload media for it 🙂"
        )
        return

    # Displays a window for the user to select which Deck they want to upload media for.
    # This will only display Decks that the user owns AND are installed locally. Maintainers
    # and subscribers should not be able to upload media for Decks they maintain/subscribe to.
    ah_did = choose_ankihub_deck(
        "Choose the AnkiHub deck for which<br>you want to upload media.",
        parent=aqt.mw,
        ah_dids=owned_ah_dids,
    )
    if ah_did is None:
        return

    deck_config = config.deck_config(ah_did)
    deck_name = deck_config.name

    nids = ankihub_db.anki_nids_for_ankihub_deck(ah_did)
    if not nids:
        showInfo("You can't upload media for an empty deck.")
        return

    media_names = ankihub_db.media_names_for_ankihub_deck(ah_did=ah_did)

    # Check if the deck references any media files, if it does
    # not, no point on trying to upload it
    if not media_names:
        showInfo("This deck has no media to upload.")
        return

    media_dir = Path(aqt.mw.col.media.dir())
    media_names_with_existing_files = [
        media_name for media_name in media_names if (media_dir / media_name).is_file()
    ]

    # Check if the files referenced by the deck exists locally, if none exist, no point in uploading.
    if not media_names_with_existing_files:
        showInfo(
            "You can't upload media for this deck because none of the referenced media files are present in your "
            "local media folder."
        )
        return

    confirm = ask_user(
        f"Uploading all media files for the deck <b>{deck_name}</b> to AnkiHub "
        "might take a while depending on the number of media files that the deck uses.<br><br>"
        "Would you like to continue?",
    )
    if not confirm:
        return

    def on_success() -> None:
        showInfo(f"🎉 Successfuly uploaded all media for<br><b>{deck_config.name}</b>!")

    # Extract the AnkiHub deck ID using a sample note id
    ah_did = ankihub_db.ankihub_did_for_anki_nid(nids[0])

    media_sync.start_media_upload(
        media_names_with_existing_files, ah_did, on_success=on_success
    )

    showInfo(
        "🖼️ Upload started! You can continue using Anki in the meantime."
        "<br><br>We'll notify you when the upload process finishes 👍"
    )


def _config_setup(parent: QMenu) -> None:
    config_action = QAction("⚙️ Config", parent)
    qconnect(config_action.triggered, get_config_dialog_manager().open_config)
    parent.addAction(config_action)


def _ankihub_help_setup(parent: QMenu):
    """Set up the sub menu for help related items."""
    help_menu = QMenu("🆘 Help", parent)

    # && is an escaped & in qt
    q_notion_action = QAction("Documentation", help_menu)
    qconnect(
        q_notion_action.triggered,
        lambda: openLink("https://community.ankihub.net/docs"),
    )
    help_menu.addAction(q_notion_action)

    q_get_help_action = QAction("Get Help", help_menu)
    qconnect(
        q_get_help_action.triggered,
        lambda: openLink("https://community.ankihub.net/c/support"),
    )
    help_menu.addAction(q_get_help_action)

    q_changelog_action = QAction("Changelog", help_menu)
    qconnect(
        q_changelog_action.triggered,
        lambda: openLink("https://community.ankihub.net/c/announcements/"),
    )
    help_menu.addAction(q_changelog_action)

    q_upload_logs_action = QAction("Upload logs", help_menu)
    qconnect(q_upload_logs_action.triggered, _upload_logs_action)
    help_menu.addAction(q_upload_logs_action)

    q_upload_logs_and_data_action = QAction("Upload logs and data", help_menu)
    qconnect(q_upload_logs_and_data_action.triggered, _upload_logs_and_data_action)
    help_menu.addAction(q_upload_logs_and_data_action)

    q_downgrade_from_beta_version_action = QAction(
        "Downgrade from add-on beta version", help_menu
    )
    qconnect(
        q_downgrade_from_beta_version_action.triggered, _trigger_install_release_version
    )
    help_menu.addAction(q_downgrade_from_beta_version_action)

    q_version_action = QAction(f"Version {ADDON_VERSION}", help_menu)
    q_version_action.setEnabled(False)
    help_menu.addAction(q_version_action)
    help_menu.setMinimumWidth(250)

    parent.addMenu(help_menu)


def _trigger_install_release_version():
    showInfo(
        "When you click OK, the add-on update dialog will open in a couple of seconds "
        "and you will be able to install the version of the add-on that is available on AnkiWeb.<br><br>"
        "If it doesn't show up (which can happen when you e.g. have no internet connection), "
        "try checking for add-on updates manually later."
    )

    # Set the installed_at timestamp to make Anki think that the add-on has an older version
    # installed than the one that is available on AnkiWeb.
    addon_module = aqt.mw.addonManager.addonFromModule(__name__)
    addon_meta = aqt.mw.addonManager.addon_meta(addon_module)
    addon_meta.installed_at = 0
    aqt.mw.addonManager.write_addon_meta(addon_meta)

    check_and_prompt_for_updates_on_main_window()


def _ankihub_terms_and_policy_setup(parent: QMenu):
    """Set up the sub menu for terms and policy related items."""
    terms_and_policy_menu = QMenu("🤝 Terms and Policy", parent)

    q_terms_and_conditions_action = QAction(
        "Terms && Conditions", terms_and_policy_menu
    )
    qconnect(
        q_terms_and_conditions_action.triggered,
        lambda: openLink("https://community.ankihub.net/tos"),
    )
    terms_and_policy_menu.addAction(q_terms_and_conditions_action)

    q_privacy_policy_action = QAction("Privacy Policy", terms_and_policy_menu)
    qconnect(
        q_privacy_policy_action.triggered,
        lambda: openLink("https://community.ankihub.net/privacy"),
    )
    terms_and_policy_menu.addAction(q_privacy_policy_action)

    parent.addMenu(terms_and_policy_menu)


def _ankihub_logout_setup(parent: QMenu):
    q_action = QAction("🔑 Sign out", aqt.mw)
    qconnect(q_action.triggered, _confirm_sign_out)
    parent.addAction(q_action)


def _media_sync_status_setup(parent: QMenu):
    parent._media_sync_status_action = QAction("", parent)  # type: ignore
    parent.addAction(parent._media_sync_status_action)  # type: ignore
    media_sync.set_status_action(parent._media_sync_status_action)  # type: ignore
    media_sync.refresh_sync_status_text()
