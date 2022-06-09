import copy
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

from anki.decks import DeckId
from anki.models import NotetypeId
from pytest_anki import AnkiSession


sample_model_id = NotetypeId(1650564101852)
sample_deck = str(pathlib.Path(__file__).parent / "test_data" / "small.apkg")


def test_entry_point(anki_session_with_addon: AnkiSession):
    from aqt.main import AnkiQt

    from ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)


def test_editor(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.constants import API_URL_BASE, AnkiHubCommands
    from ankihub.gui.editor import (
        ankihub_message_handler,
        on_ankihub_button_press,
        on_select_command,
        setup,
    )

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
    editor.note.fields = ["a", "b", "1"]
    editor.note.tags = ["test_tag"]

    # TODO Mock what this is actually expected to return
    expected_response = {}  # type: ignore
    requests_mock.post(
        f"{API_URL_BASE}/notes/{editor.note.id}/suggestion/",
        status_code=201,
        json=expected_response,
    )
    # This test is quite limited since we don't know how to run this test with a
    # "real," editor, instead of the manually instantiated one above. So for
    # now, this test just checks that on_ankihub_button_press runs without
    # raising any errors.
    response = on_ankihub_button_press(editor)
    assert response == {}


def test_get_note_types_in_deck(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck) as deck_id:
            # test get note types in deck
            from ankihub.utils import get_note_types_in_deck

            note_model_ids = get_note_types_in_deck(DeckId(deck_id))
            # TODO test on a deck that has more than one note type.
            assert len(note_model_ids) == 1
            assert note_model_ids == [1650564101852]


def test_note_type_contains_field(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck):
            from ankihub.constants import ANKIHUB_NOTE_TYPE_FIELD_NAME
            from ankihub.utils import note_type_contains_field

            note_type = anki_session.mw.col.models.get(sample_model_id)
            assert note_type_contains_field(note_type, sample_model_id) is False
            new_field = {"name": ANKIHUB_NOTE_TYPE_FIELD_NAME}
            note_type["flds"].append(new_field)
            assert note_type_contains_field(note_type, ANKIHUB_NOTE_TYPE_FIELD_NAME)
            note_type["flds"].remove(new_field)


def test_modify_note_type(anki_session_with_addon: AnkiSession):
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck):
            from ankihub.register_decks import modify_note_type
            from ankihub.constants import ANKIHUB_NOTE_TYPE_FIELD_NAME

            note_type = anki_session.mw.col.models.by_name("Basic")
            original_note_type = copy.deepcopy(note_type)
            original_note_template = original_note_type["tmpls"][0]["afmt"]
            modify_note_type("Basic")
            modified_template = note_type["tmpls"][0]["afmt"]
            # # TODO Make more precise assertions.
            assert ANKIHUB_NOTE_TYPE_FIELD_NAME in modified_template
            assert original_note_template != modified_template


def test_create_collaborative_deck_and_upload(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    anki_session = anki_session_with_addon

    from ankihub.constants import API_URL_BASE

    requests_mock.get(f"{API_URL_BASE}/decks/1/updates", json={"notes": []})
    with anki_session.profile_loaded():
        with anki_session.deck_installed(sample_deck) as deck_id:

            from ankihub.register_decks import AnkiHubClient, create_collaborative_deck

            deck_name = anki_session.mw.col.decks.name(DeckId(deck_id))
            with monkeypatch.context() as m:
                m.setattr(AnkiHubClient, "upload_deck", Mock())
                create_collaborative_deck(deck_name)

                requests_mock.get(
                    f"{API_URL_BASE}/decks/pre-signed-url",
                    status_code=200,
                    json={"pre_signed_url": "http://fake_url"},
                )
                requests_mock.put(
                    "http://fake_url",
                    status_code=200,
                )
                requests_mock.post(f"{API_URL_BASE}/decks/", status_code=201)

                from ankihub.register_decks import upload_deck

                with patch("ankihub.ankihub_client.Config"):
                    monkeypatch.setattr(
                        "ankihub.ankihub_client.requests", requests_mock
                    )
                    upload_deck(DeckId(deck_id))


def test_client_login_and_signout(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()
    credentials_data = {"username": "test", "password": "testpassword"}
    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    client.login(credentials=credentials_data)
    assert client._headers["Authorization"] == "Token f4k3t0k3n"

    # test signout
    client.signout()
    assert client._headers["Authorization"] == ""
    assert client._config.private_config.token == ""


def test_upload_deck(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()

    requests_mock.get(
        f"{API_URL_BASE}/decks/pre-signed-url",
        status_code=200,
        json={"pre_signed_url": "http://fake_url"},
    )
    requests_mock.put(
        "http://fake_url",
        status_code=200,
    )
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=201)

    # test upload deck
    response = client.upload_deck(pathlib.Path(sample_deck))
    assert response.status_code == 201

    # test upload deck unauthenticated
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    response = client.upload_deck(pathlib.Path(sample_deck))
    assert response.status_code == 403


def test_get_deck_updates(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()

    # test get deck updates
    deck_id = 1
    date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)

    timestamp = date_object.timestamp()
    expected_data = {
        "total": 1,
        "current_page": 1,
        "has_next": False,
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
        "protected_fields": {"Basic": ["Back"]},
        "protected_tags": ["Test"],
    }

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", json=expected_data)
    for response in client.get_deck_updates(deck_id=str(deck_id)):
        assert response.json() == expected_data

    # test get deck updates unauthenticated
    deck_id = 1
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", status_code=403)
    for response in client.get_deck_updates(deck_id=str(deck_id)):
        assert response.status_code == 403


def test_get_deck_by_id(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()

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
    response = client.get_deck_by_id(deck_id=str(deck_id))
    assert response.json() == expected_data

    # test get deck by id unauthenticated
    deck_id = DeckId(1)
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.get_deck_by_id(deck_id=str(deck_id))
    assert response.status_code == 403


def test_get_note_by_anki_id(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()

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
    response = client.get_note_by_anki_id(anki_id=str(note_anki_id))
    assert response.json() == expected_data

    # test get note by anki id unauthenticated
    note_anki_id = 1
    requests_mock.get(f"{API_URL_BASE}/notes/{note_anki_id}", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.get_note_by_anki_id(anki_id=str(note_anki_id))
    assert response.status_code == 403


def test_create_change_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()
    # test create change note suggestion
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=201)
    response = client.create_change_note_suggestion(
        ankihub_id=str(1),
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 201

    # test create change note suggestion unauthenticated
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=403)
    monkeypatch.setattr("ankihub.ankihub_client.showText", Mock())
    response = client.create_change_note_suggestion(
        ankihub_id=str(1),
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 403


def test_create_new_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient()
    # test create new note suggestion
    deck_id = 1
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=201
    )
    response = client.create_new_note_suggestion(
        deck_id=deck_id,
        anki_id=1,
        ankihub_id=str(1),
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
        ankihub_id=str(1),
        anki_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
    )
    assert response.status_code == 403
