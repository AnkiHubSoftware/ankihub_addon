from unittest.mock import MagicMock

from pytest_anki import AnkiSession


def test_editor(anki_session_with_addon: AnkiSession, monkeypatch):
    import ankihub.gui.editor as editor
    from ankihub.constants import AnkiHubCommands

    anki_editor = editor.setup()
    assert anki_editor.ankihub_command == "Suggest a change"
    editor.on_select_command(anki_editor, AnkiHubCommands.NEW.value)
    assert anki_editor.ankihub_command == "Suggest a new note"
    editor.ankihub_message_handler(
        tuple(),
        f"ankihub:{AnkiHubCommands.CHANGE.value}",
        anki_editor,
    )
    assert anki_editor.ankihub_command == "Suggest a change"
    # Patch the editor so that it has the note attribute, which it will have when
    # the editor is actually instantiated during an Anki Desktop session.
    mock = MagicMock()
    anki_editor.note = mock
    mock.id = 123
    _ = editor.on_ankihub_button_press(anki_editor)
    # TODO assert response
