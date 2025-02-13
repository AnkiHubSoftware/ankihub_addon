"""Dialog for managing LLM API secrets."""

import json
import subprocess
from pathlib import Path

from aqt.qt import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from aqt.utils import showWarning, tooltip

from ...gui.utils import active_window_or_mw


class SecretsDialog(QDialog):
    """Dialog for managing LLM API secrets."""

    def __init__(self):
        super().__init__(parent=active_window_or_mw())
        self.setWindowTitle("LLM API Secrets")
        self.setMinimumWidth(500)
        try:
            self.keys_file = self._get_keys_file_path()
        except Exception as e:
            showWarning(str(e))
            self.reject()
            return
        self.current_keys = self._load_current_keys()
        self._setup_ui()

    def _get_keys_file_path(self) -> Path:
        """Get the path to the keys.json file."""
        try:
            result = subprocess.run(
                ["uv", "run", "--no-project", "llm", "keys", "path"],
                capture_output=True,
                text=True,
                check=True,
            )
            return Path(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            raise Exception(
                "Failed to get LLM keys path. Please run 'llm setup' in your terminal first.\n\n"
                f"Error: {e.stderr}"
            )
        except Exception as e:
            raise Exception(f"Unexpected error getting LLM keys path: {str(e)}")

    def _load_current_keys(self) -> dict:
        """Load current API keys from the keys file."""
        if not self.keys_file.exists():
            return {}
        try:
            return json.loads(self.keys_file.read_text())
        except json.JSONDecodeError:
            return {}

    def _save_keys(self) -> None:
        """Save API keys to the keys file."""
        if not self.keys_file.parent.exists():
            showWarning(
                "Cannot save API keys: The llm config directory does not exist.\n"
                "Please run 'llm setup' in your terminal first."
            )
            return

        try:
            self.keys_file.write_text(json.dumps(self.current_keys, indent=2))
            tooltip("API keys saved successfully")
        except (OSError, IOError) as e:
            showWarning(f"Failed to save API keys: {str(e)}")

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout()

        # Add description
        description = QLabel(
            "Enter your API keys for the LLM providers below. "
            "Keys are stored securely in your local config."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        # Create input fields for each provider
        providers = {
            "gemini": "Google Gemini API Key",
            "perplexity": "Perplexity API Key",
            "claude": "Anthropic (Claude) API Key",
            "openai": "OpenAI API Key",
        }

        self.key_inputs = {}
        for provider_id, provider_name in providers.items():
            # Create a horizontal layout for each provider
            provider_layout = QHBoxLayout()

            # Add label
            label = QLabel(f"{provider_name}:")
            provider_layout.addWidget(label)

            # Add secure input field
            input_field = QLineEdit()
            input_field.setEchoMode(QLineEdit.EchoMode.Password)
            if provider_id in self.current_keys:
                input_field.setText(self.current_keys[provider_id])
                # Show a small indicator that a key exists
                label.setText(f"{provider_name}: 🔑")
            self.key_inputs[provider_id] = input_field
            provider_layout.addWidget(input_field)

            # Add show/hide button
            toggle_btn = QPushButton("👁️")
            toggle_btn.setFixedWidth(30)
            toggle_btn.clicked.connect(
                lambda checked, field=input_field: self._toggle_password_visibility(
                    field
                )
            )
            provider_layout.addWidget(toggle_btn)

            layout.addLayout(provider_layout)

        # Add save button
        save_btn = QPushButton("Save Keys")
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

        self.setLayout(layout)

    def _toggle_password_visibility(self, field: QLineEdit) -> None:
        """Toggle the visibility of the password field."""
        if field.echoMode() == QLineEdit.EchoMode.Password:
            field.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            field.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_save(self) -> None:
        """Handle saving the API keys."""
        for provider_id, input_field in self.key_inputs.items():
            key = input_field.text().strip()
            if key:
                self.current_keys[provider_id] = key
            elif provider_id in self.current_keys:
                del self.current_keys[provider_id]

        self._save_keys()
