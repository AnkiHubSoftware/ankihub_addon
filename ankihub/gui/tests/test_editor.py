from unittest.mock import MagicMock

from ankihub.constants import API_URL_BASE
from pytest_anki import AnkiSession


def test_editor(anki_session_with_addon: AnkiSession, monkeypatch, requests_mock):
    import ankihub.gui.editor as editor
    from ankihub.constants import AnkiHubCommands

    anki_editor = editor.setup()
    # Check the default command.
    assert anki_editor.ankihub_command == "Suggest a change"
    editor.on_select_command(anki_editor, AnkiHubCommands.NEW.value)
    # Check that the command was updated.
    assert anki_editor.ankihub_command == "Suggest a new note"
    editor.ankihub_message_handler(
        (False, None),
        f"ankihub:{AnkiHubCommands.CHANGE.value}",
        anki_editor,
    )
    assert anki_editor.ankihub_command == "Suggest a change"
    # Patch the editor so that it has the note attribute, which it will have when
    # the editor is actually instantiated during an Anki Desktop session.
    anki_editor.note = MagicMock()
    anki_editor.mw = MagicMock()
    anki_editor.note.id = 123
    anki_editor.note.data = {
        "tags": ["test"],
        "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
    }

    requests_mock.post(
        f"{API_URL_BASE}/notes/{anki_editor.note.id}/suggestion/", status_code=201
    )
    response = editor.on_ankihub_button_press(anki_editor)
    assert response.status_code == 201
