"""Sets up the config dialog for the add-on.

Note about non-standard imports: We don't import from ankiaddonconfig on top of the file because that would break tests
as aqt.mw is not always set up when running tests and ankiaddonconfig.window imports mw from aqt on top of the file
so mw ends up being None in ankiaddonconfig.window.
By doing the import inside the function we make sure that aqt.mw is set up when we import ankiaddonconfig.window
during normal execution.
(We access mw using aqt.mw across the codebase to prevent this problem.)"""

from typing import cast

from aqt import qconnect
from aqt.qt import QCheckBox, Qt

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

    if config.get_feature_flags().get("mh_integration"):
        tab.text("Sidebar", bold=True)
        tab.checkbox("ankihub_ai_chatbot", "AnkiHub AI Chatbot")

        add_nested_checkboxes(
            tab, key_prefix="boards_and_beyond", description="Boards and Beyond"
        )
        add_nested_checkboxes(
            tab, key_prefix="first_aid_forward", description="First Aid Forward"
        )

        tab.hseparator()
        tab.space(8)

    tab.text("Debug", bold=True)
    tab.checkbox("report_errors", "Report errors")
    tab.checkbox("debug_level_logs", "Verbose logs (restart required)")

    tab.stretch()


def add_nested_checkboxes(config_layout, key_prefix: str, description: str) -> None:

    from .ankiaddonconfig.window import ConfigLayout

    config_layout = cast(ConfigLayout, config_layout)

    main_checkbox = QCheckBox(description)
    config_layout.addWidget(main_checkbox)

    container_outer = config_layout.hcontainer()
    container_outer.setContentsMargins(0, 2, 0, 2)

    container_inner = container_outer.vcontainer()
    container_inner.setContentsMargins(30, 0, 0, 0)

    step_1_checkbox = container_inner.checkbox(
        f"{key_prefix}_step_1", description="USMLE - Step 1"
    )
    step_2_checkbox = container_inner.checkbox(
        f"{key_prefix}_step_2", description="USMLE - Step 2"
    )

    def update_main_checkbox() -> None:
        checkboxes = [step_1_checkbox, step_2_checkbox]
        checked_count = sum(checkbox.isChecked() for checkbox in checkboxes)

        if checked_count == 0:
            main_checkbox.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == len(checkboxes):
            main_checkbox.setCheckState(Qt.CheckState.Checked)
        else:
            main_checkbox.setCheckState(Qt.CheckState.PartiallyChecked)

    def on_main_checkbox_clicked() -> None:
        is_checked = main_checkbox.checkState() != Qt.CheckState.Unchecked
        main_checkbox.setChecked(is_checked)
        step_1_checkbox.setChecked(is_checked)
        step_2_checkbox.setChecked(is_checked)

    qconnect(step_1_checkbox.stateChanged, update_main_checkbox)
    qconnect(step_2_checkbox.stateChanged, update_main_checkbox)
    qconnect(main_checkbox.clicked, on_main_checkbox_clicked)
