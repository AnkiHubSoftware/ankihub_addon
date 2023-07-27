from .ankiaddonconfig import ConfigManager, ConfigWindow

config_dialog_manager = ConfigManager()


def setup_config():
    config_dialog_manager.use_custom_window()
    config_dialog_manager.add_config_tab(_general_tab)


def _general_tab(conf_window: ConfigWindow):
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
    tab.dropdown(
        "suspend_new_cards_of_existing_notes",
        labels=["If sibling cards are suspended", "Always", "Never"],
        values=["if_siblings_are_suspended", "always", "never"],
        description="Suspend new cards of existing notes",
    )
    tab.hseparator()
    tab.space(8)

    tab.text("Debug", bold=True)
    tab.checkbox("report_errors", "Report errors")
    tab.checkbox("debug_level_logs", "Verbose logs (restart required)")

    tab.stretch()
