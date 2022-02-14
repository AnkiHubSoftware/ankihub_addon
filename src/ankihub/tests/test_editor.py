from pytest_anki import AnkiSession


def test_editor(anki_session_with_addon: AnkiSession):
    from aqt.editor import Editor

    from ankihub.constants import AnkiHubCommands
    from ankihub.gui.editor import (
        on_ankihub_button_press,
        on_bridge_command,
        on_select_command,
        setup,
    )

    editor = setup()
    assert editor.ankihub_command == "Suggest a change"
    on_select_command(editor, AnkiHubCommands.NEW.value)
    assert editor.ankihub_command == "Suggest a new note"
    on_bridge_command(editor, f"ankihub:{AnkiHubCommands.CHANGE.value}", lambda: None)
    assert editor.ankihub_command == "Suggest a change"
    response = on_ankihub_button_press(editor)
    # TODO assert response
