"""Module for managing LLM API secrets."""

from .dialog import SecretsDialog


def open_secrets_dialog() -> None:
    """Open the secrets management dialog."""
    dialog = SecretsDialog()
    dialog.exec()
