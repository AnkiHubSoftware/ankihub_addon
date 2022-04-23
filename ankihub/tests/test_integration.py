from unittest.mock import MagicMock

from pytest_anki import AnkiSession

from ankihub.constants import API_URL_BASE


def test_integration(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
    """Make it easy on ourselves and dump all of our tests that require an Anki here.

    Unfortunately, using pytest-anki is incredibly fickle due to the instability of its
    dependencies (ahem, Anki) and running a single integration test that relies on
    Anki is far more reliable than multiple tests that use an AnkiSession.
    """
    session = anki_session_with_addon
    from aqt.main import AnkiQt
    from ankihub import entry_point

    # Begin test entry point
    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)
    # End test entry point

    # Begin test editor
    from ankihub.gui.editor import setup, on_select_command, on_ankihub_button_press, ankihub_message_handler
    from ankihub.constants import AnkiHubCommands

    editor = setup()
    # Check the default command.
    assert editor.ankihub_command == "Suggest a change"
    on_select_command(editor, AnkiHubCommands.NEW.value)
    # Check that the command was updated.
    assert editor.ankihub_command == "Suggest a new note"
    ankihub_message_handler(
        (False, None),
        f"ankihub:{AnkiHubCommands.CHANGE.value}",
        editor,
    )
    assert editor.ankihub_command == "Suggest a change"
    # Patch the editor so that it has the note attribute, which it will have when
    # the editor is actually instantiated during an Anki Desktop session.
    editor.mw = MagicMock()
    editor.note = MagicMock()
    editor.note.id = 1
    editor.note.fields = ["1", "a", "b"]
    editor.note.tags = ["test_tag"]
    requests_mock.post(
        f"{API_URL_BASE}/notes/{editor.note.id}/suggestion/",
        status_code=201,

    )
    # This test is quite limited since we don't know how to run this test with a
    # "real," editor, instead of the manually instantiated one above. So for
    # now, this test just checks that on_ankihub_button_press runs without
    # raising any errors.
    response = on_ankihub_button_press(editor)
    assert response.status_code == 201
    # End test editor
