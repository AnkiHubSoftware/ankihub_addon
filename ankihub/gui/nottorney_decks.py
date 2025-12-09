"""Dialog to view and download purchased Nottorney decks."""

import os
from pathlib import Path
from typing import Dict, List, Optional

import aqt
from aqt.qt import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    Qt,
    qconnect,
)
from aqt.utils import showInfo
from anki.importing.apkg import AnkiPackageImporter  # type: ignore

from .. import LOGGER
from ..nottorney_client import NottorneyClient, NottorneyHTTPError
from ..settings import config


class NottorneyDecksDialog(QDialog):
    """Dialog for viewing and downloading purchased Nottorney decks."""

    _window: Optional["NottorneyDecksDialog"] = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("My Nottorney Decks")
        self.setMinimumSize(500, 400)

        token = config.nottorney_token()
        if not token:
            QMessageBox.warning(self, "Error", "Please login first")
            self.reject()
            return

        self.client = NottorneyClient(token=token)
        self.decks: List[Dict] = []
        self._setup_ui()
        self._load_decks()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Header
        user_email = config.nottorney_user_email() or "Unknown"
        header = QLabel(f"Logged in as: {user_email}")
        layout.addWidget(header)

        # Decks list
        self.decks_list = QListWidget()
        self.decks_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        qconnect(self.decks_list.itemDoubleClicked, self._on_download)
        layout.addWidget(self.decks_list)

        # Progress bar (hidden initially)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Buttons
        button_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        qconnect(self.refresh_btn.clicked, self._load_decks)
        button_layout.addWidget(self.refresh_btn)

        self.download_btn = QPushButton("Download Selected")
        qconnect(self.download_btn.clicked, self._on_download)
        button_layout.addWidget(self.download_btn)

        self.close_btn = QPushButton("Close")
        qconnect(self.close_btn.clicked, self.accept)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _load_decks(self):
        """Load the list of purchased decks."""
        try:
            self.decks_list.clear()
            self.refresh_btn.setEnabled(False)
            self.decks = self.client.get_purchased_decks()

            if not self.decks:
                item = QListWidgetItem("No purchased decks found")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.decks_list.addItem(item)
            else:
                for deck in self.decks:
                    title = deck.get("title", "Unknown Deck")
                    card_count = deck.get("card_count", "?")
                    description = deck.get("description", "")
                    item_text = f"📚 {title} ({card_count} cards)"
                    if description:
                        item_text += f"\n   {description}"

                    item = QListWidgetItem(item_text)
                    # Store deck data in item (Qt.UserRole = 256)
                    item.setData(256, deck)
                    self.decks_list.addItem(item)

            LOGGER.info("Loaded purchased decks", count=len(self.decks))

        except NottorneyHTTPError as e:
            if e.status_code == 401:
                QMessageBox.warning(
                    self,
                    "Session Expired",
                    "Your session has expired. Please login again.",
                )
                config.clear_nottorney_credentials()
                self.reject()
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to load decks: {e}\n\nStatus code: {e.status_code}",
                )
            LOGGER.error("Failed to load decks", status_code=e.status_code)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load decks: {str(e)}")
            LOGGER.exception("Unexpected error loading decks")
        finally:
            self.refresh_btn.setEnabled(True)

    def _on_download(self):
        """Handle deck download."""
        item = self.decks_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Error", "Please select a deck to download")
            return

        deck = item.data(256)
        if not deck or not isinstance(deck, dict):
            return

        product_id = deck.get("id")
        if not product_id:
            QMessageBox.warning(self, "Error", "Invalid deck data")
            return

        deck_title = deck.get("title", "Unknown Deck")

        # Confirm download
        reply = QMessageBox.question(
            self,
            "Download Deck",
            f"Download and import '{deck_title}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.download_btn.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            self.progress.setVisible(True)
            self.progress.setRange(0, 0)  # Indeterminate

            # Download to temp file
            temp_path = Path(aqt.mw.pm.profileFolder()) / "nottorney_temp.apkg"
            if temp_path.exists():
                temp_path.unlink()

            LOGGER.info("Downloading deck", deck_title=deck_title, product_id=product_id)
            self.client.download_deck(product_id, temp_path)

            # Import into Anki
            self.progress.setRange(0, 100)
            self.progress.setValue(50)

            LOGGER.info("Importing deck into Anki", deck_title=deck_title)
            importer = AnkiPackageImporter(aqt.mw.col, str(temp_path))
            importer.run()

            self.progress.setValue(100)

            # Cleanup
            if temp_path.exists():
                try:
                    os.remove(temp_path)
                except Exception as e:
                    LOGGER.warning("Failed to remove temp file", path=str(temp_path), error=str(e))

            QMessageBox.information(
                self,
                "Success",
                f"Deck '{deck_title}' imported successfully!",
            )

            # Refresh Anki UI
            aqt.mw.reset()

            LOGGER.info("Deck imported successfully", deck_title=deck_title)

        except NottorneyHTTPError as e:
            if e.status_code == 403:
                QMessageBox.warning(
                    self,
                    "Error",
                    "You have not purchased this deck.",
                )
            elif e.status_code == 401:
                QMessageBox.warning(
                    self,
                    "Session Expired",
                    "Your session has expired. Please login again.",
                )
                config.clear_nottorney_credentials()
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Download failed: {e}\n\nStatus code: {e.status_code}",
                )
            LOGGER.error("Download failed", status_code=e.status_code, product_id=product_id)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Import failed: {str(e)}",
            )
            LOGGER.exception("Unexpected error during download/import")
        finally:
            self.download_btn.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self.progress.setVisible(False)

    @classmethod
    def display_decks(cls, parent=None):
        """Display the decks dialog."""
        if not config.is_nottorney_logged_in():
            from .nottorney_login import NottorneyLoginDialog

            login_dialog = NottorneyLoginDialog.display_login(parent)
            if login_dialog.exec() != QDialog.DialogCode.Accepted:
                return None

        if cls._window is None:
            cls._window = cls(parent)
        else:
            cls._window._load_decks()
            cls._window.activateWindow()
            cls._window.raise_()
            cls._window.show()

        LOGGER.info("Showed Nottorney decks dialog.")
        return cls._window

