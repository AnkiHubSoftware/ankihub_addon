"""Sets up the config dialog for the add-on.

Note about non-standard imports: We don't import from ankiaddonconfig on top of the file because that would break tests
as aqt.mw is not always set up when running tests and ankiaddonconfig.window imports mw from aqt on top of the file
so mw ends up being None in ankiaddonconfig.window.
By doing the import inside the function we make sure that aqt.mw is set up when we import ankiaddonconfig.window
during normal execution.
(We access mw using aqt.mw across the codebase to prevent this problem.)"""
from typing import cast

from ..settings import config

_config_dialog_manager = None


def setup_config_dialog_manager():
    from .ankiaddonconfig import ConfigManager

    global _config_dialog_manager
    _config_dialog_manager = ConfigManager()
    _config_dialog_manager.use_custom_window()
    _config_dialog_manager.add_config_tab(_general_tab)


def get_config_dialog_manager():
    return _config_dialog_manager


def _general_tab(conf_window) -> None:
    from .ankiaddonconfig import ConfigWindow

    conf_window = cast(ConfigWindow, conf_window)

    # Refresh config when window is closed
    conf_window.execute_on_close(lambda: config.load_public_config())

    tab = conf_window.add_tab("General")

    tab.text("Shortcuts", bold=True)
    tab.shortcut_input("sync_hotkey", "Sync with AnkiHub")
    tab.shortcut_input("hotkey", "Create suggestion for note")
    tab.hseparator()
    tab.space(8)

    tab.text("Sync", bold=True)
    tab.dropdown(
        "auto_sync",
        labels=["On AnkiWeb Sync", "On Anki start", "Never"],
        values=["on_ankiweb_sync", "on_startup", "never"],
        description="Auto Sync with AnkiHub",
    )
    tab.hseparator()
    tab.space(8)

    tab.text("Debug", bold=True)
    tab.checkbox("report_errors", "Report errors")
    tab.checkbox("debug_level_logs", "Verbose logs (restart required)")

    tab.stretch()
