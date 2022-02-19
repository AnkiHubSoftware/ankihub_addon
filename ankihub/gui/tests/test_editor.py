from pytest_anki import AnkiSession


def test_editor(anki_session_with_addon: AnkiSession):
    from ankihub.constants import AnkiHubCommands
    import ankihub.gui.editor as editor

    anki_editor = editor.setup()
    assert anki_editor.ankihub_command == "Suggest a change"
    editor.on_select_command(anki_editor, AnkiHubCommands.NEW.value)
    assert anki_editor.ankihub_command == "Suggest a new note"
    editor.on_bridge_command(anki_editor, f"ankihub:{AnkiHubCommands.CHANGE.value}", lambda: None)
    assert anki_editor.ankihub_command == "Suggest a change"
    response = editor.on_ankihub_button_press(anki_editor)
    # TODO assert response
