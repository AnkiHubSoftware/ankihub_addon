from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, Mock

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

    # Begin test client
    # test login

    from ankihub.ankihub_client import AnkiHubClient
    client = AnkiHubClient()
    credentials_data = {"username": "test", "password": "testpassword"}
    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    client.login(credentials=credentials_data)
    assert client._headers["Authorization"] == "Token f4k3t0k3n"

    # test signout
    client.signout()
    assert client._headers["Authorization"] == ""
    assert client._config.private_config.token == ""

    # test upload deck
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=201)
    response = client.upload_deck("test.apkg")
    assert response.status_code == 201

    # test upload deck unauthenticated
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    response = client.upload_deck("test.apkg")
    assert response.status_code == 403

    # test get deck updates
    deck_id = 1
    date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)

    timestamp = date_object.timestamp()
    expected_data = {
        "since": timestamp,
        "notes": [
            {
                "deck_id": deck_id,
                "note_id": 1,
                "anki_id": 1,
                "tags": ["New Tag"],
                "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
            }
        ],
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", json=expected_data)
    conf = client._config.private_config
    response = client.get_deck_updates(deck_id=deck_id)
    assert response.json() == expected_data
    assert conf.last_sync

    # test get deck updates unauthenticated
    deck_id = 1
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", status_code=403)
    response = client.get_deck_updates(deck_id=deck_id)
    assert response.status_code == 403

    # test get deck by id
    deck_id = 1
    date_time_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    expected_data = {
        "id": deck_id,
        "name": "test",
        "owner": 1,
        "anki_id": 1,
        "csv_last_upload": date_time_str,
        "csv_notes_url": "http://fake-csv-url.com/test.csv",
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", json=expected_data)
    response = client.get_deck_by_id(deck_id=deck_id)
    assert response.json() == expected_data

    # test get deck by id unauthenticated
    deck_id = 1
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.get_deck_by_id(deck_id=deck_id)
    assert response.status_code == 403

    # test get note by anki id
    note_anki_id = 1
    expected_data = {
        "deck_id": 1,
        "note_id": 1,
        "anki_id": 1,
        "tags": ["New Tag"],
        "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
    }
    requests_mock.get(f"{API_URL_BASE}/notes/{note_anki_id}", json=expected_data)
    response = client.get_note_by_anki_id(anki_id=note_anki_id)
    assert response.json() == expected_data

    # test get note by anki id unauthenticated
    note_anki_id = 1
    requests_mock.get(f"{API_URL_BASE}/notes/{note_anki_id}", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.get_note_by_anki_id(anki_id=note_anki_id)
    assert response.status_code == 403

    # test create change note suggestion
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=201)
    response = client.create_change_note_suggestion(
        ankihub_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 201

    # test create change note suggestion unauthenticated
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.create_change_note_suggestion(
        ankihub_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 403

    # test create new note suggestion
    deck_id = 1
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=201
    )
    response = client.create_new_note_suggestion(
        deck_id=deck_id,
        anki_id=1,
        ankihub_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 201

    # test create new note suggestion unauthenticated
    deck_id = 1
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=403
    )
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.create_new_note_suggestion(
        deck_id=deck_id,
        ankihub_id=1,
        anki_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 403
    # End test client
