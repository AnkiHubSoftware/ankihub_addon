"""Login dialog for Nottorney authentication."""

import re
from typing import Optional

import aqt
from aqt.qt import QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, Qt, qconnect
from aqt.utils import tooltip

from .. import LOGGER
from ..nottorney_client import NottorneyClient, NottorneyHTTPError
from ..settings import config


class NottorneyLoginDialog(QDialog):
    """Dialog for Nottorney user authentication."""

    _window: Optional["NottorneyLoginDialog"] = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nottorney Login")
        self.setMinimumWidth(350)
        self.client = NottorneyClient()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Email
        email_label = QLabel("Email:")
        layout.addWidget(email_label)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("your@email.com")
        self.email_input.setMinimumWidth(300)
        qconnect(self.email_input.returnPressed, self._on_login)
        layout.addWidget(self.email_input)

        # Password
        password_label = QLabel("Password:")
        layout.addWidget(password_label)
        password_layout = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumWidth(300)
        qconnect(self.password_input.returnPressed, self._on_login)
        password_layout.addWidget(self.password_input)

        self.toggle_button = QPushButton("Show")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setFixedHeight(30)
        qconnect(self.toggle_button.toggled, self._refresh_password_visibility)
        password_layout.addWidget(self.toggle_button)
        layout.addLayout(password_layout)

        # Buttons
        button_layout = QHBoxLayout()
        self.login_btn = QPushButton("Login")
        self.login_btn.setDefault(True)
        qconnect(self.login_btn.clicked, self._on_login)
        button_layout.addWidget(self.login_btn)

        self.cancel_btn = QPushButton("Cancel")
        qconnect(self.cancel_btn.clicked, self.reject)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def _refresh_password_visibility(self) -> None:
        """Toggle password visibility."""
        if self.toggle_button.isChecked():
            self.password_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_button.setText("Hide")
        else:
            self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_button.setText("Show")

    def _on_login(self):
        """Handle login button click."""
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            QMessageBox.warning(self, "Error", "Please enter email and password")
            return

        if not self._is_email(email):
            QMessageBox.warning(self, "Error", "Please enter a valid email address")
            return

        try:
            self.login_btn.setEnabled(False)
            self.login_btn.setText("Logging in...")

            result = self.client.login(email, password)

            # Save credentials
            access_token = result.get("access_token")
            user_data = result.get("user", {})
            purchased_decks = result.get("purchased_decks", [])

            if not access_token:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Login failed: No access token received from server.",
                )
                LOGGER.error("Login response missing access_token", email=email)
                return

            config.save_nottorney_token(access_token)
            config.save_nottorney_user_email(user_data.get("email", email))
            config.save_nottorney_user_id(user_data.get("id", ""))

            LOGGER.info(
                "User logged into Nottorney",
                email=email,
                purchased_decks_count=len(purchased_decks),
            )

            QMessageBox.information(
                self,
                "Success",
                f"Logged in as {email}\nFound {len(purchased_decks)} purchased deck(s)",
            )

            self.accept()

        except NottorneyHTTPError as e:
            if e.status_code == 401:
                QMessageBox.warning(self, "Error", "Invalid email or password")
            else:
                QMessageBox.warning(self, "Error", f"Login failed: {e}")
            LOGGER.error("Nottorney login failed", status_code=e.status_code)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Login failed: {str(e)}")
            LOGGER.exception("Unexpected error during Nottorney login")
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("Login")

    def _is_email(self, value: str) -> bool:
        """Check if the value is a valid email address."""
        return bool(
            re.fullmatch(
                r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*$",
                value,
            )
        )

    def clear_fields(self):
        """Clear all input fields."""
        self.email_input.setText("")
        self.password_input.setText("")

    @classmethod
    def display_login(cls, parent=None):
        """Display the login dialog.
        
        Returns:
            The dialog result code (QDialog.DialogCode.Accepted or QDialog.DialogCode.Rejected)
        """
        if cls._window is None:
            cls._window = cls(parent)
        else:
            cls._window.clear_fields()
            cls._window.activateWindow()
            cls._window.raise_()

        LOGGER.info("Showed Nottorney login dialog.")
        return cls._window.exec()  # Modal dialog behavior

