import copy
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock

from anki.decks import DeckId
from anki.models import NotetypeId
from aqt import gui_hooks
from pytest_anki import AnkiSession

sample_model_id = NotetypeId(1650564101852)
sample_deck = str(pathlib.Path(__file__).parent / "test_data" / "small.apkg")


def test_entry_point(anki_session_with_addon: AnkiSession):
    from aqt.main import AnkiQt

    from ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)


def test_editor(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
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
    monkeypatch.setattr("ankihub.gui.editor.SuggestionDialog.exec", Mock())
    on_ankihub_button_press(editor)


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
            from ankihub.constants import ANKIHUB_NOTE_TYPE_FIELD_NAME
            from ankihub.register_decks import modify_note_type

            note_type = anki_session.mw.col.models.by_name("Basic")
            original_note_type = copy.deepcopy(note_type)
            original_note_template = original_note_type["tmpls"][0]["afmt"]
            modify_note_type(note_type)
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

    monkeypatch.setattr("ankihub.utils.sync_with_ankihub", Mock())
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

                upload_deck(DeckId(deck_id))


def test_client_login_and_signout(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.addon_ankihub_client import sign_in_hook, sign_out_hook
    from ankihub.config import config
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[sign_in_hook, sign_out_hook])
    credentials_data = {"username": "test", "password": "testpassword"}
    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    requests_mock.post(
        f"{API_URL_BASE}/logout/", json={"token": "f4k3t0k3n"}, status_code=204
    )
    client.login(credentials=credentials_data)
    assert client.session.headers["Authorization"] == "Token f4k3t0k3n"
    assert config.private_config.token == "f4k3t0k3n"

    # test signout
    client.signout()
    assert client.session.headers["Authorization"] == ""
    assert config.private_config.token == ""


def test_upload_deck(anki_session_with_addon: AnkiSession, requests_mock, monkeypatch):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

    requests_mock.get(
        f"{API_URL_BASE}/decks/pre-signed-url",
        status_code=200,
        json={"pre_signed_url": "http://fake_url"},
    )
    requests_mock.put(
        "http://fake_url",
        status_code=200,
    )
    requests_mock.post(
        f"{API_URL_BASE}/decks/",
        status_code=201,
        json={"anki_id": 1, "key": "small.apkg"},
    )

    # test upload deck
    response = client.upload_deck(file=pathlib.Path(sample_deck), anki_id=1)
    assert response.status_code == 201

    # test upload deck unauthenticated
    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    response = client.upload_deck(pathlib.Path(sample_deck), anki_id=1)
    assert response.status_code == 403


def test_get_deck_updates(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

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
    for response in client.get_deck_updates(deck_id=str(deck_id), since=timestamp):
        assert response.json() == expected_data

    # test get deck updates unauthenticated
    deck_id = 1
    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", status_code=403)
    for response in client.get_deck_updates(deck_id=str(deck_id), since=timestamp):
        assert response.status_code == 403


def test_get_deck_by_id(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

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
    response = client.get_deck_by_id(deck_id=str(deck_id))
    assert response.status_code == 403


def test_get_note_by_anki_id(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE

    client = AnkiHubClient(hooks=[])

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
    response = client.get_note_by_anki_id(anki_id=note_anki_id)
    assert response.status_code == 403


def test_create_change_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create change note suggestion
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=201)
    response = client.create_change_note_suggestion(
        ankihub_note_uuid=str(1),
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CONTENT,
        comment="",
    )
    assert response.status_code == 201

    # test create change note suggestion unauthenticated
    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=403)
    response = client.create_change_note_suggestion(
        ankihub_note_uuid=str(1),
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CONTENT,
        comment="",
    )
    assert response.status_code == 403


def test_create_new_note_suggestion(
    anki_session_with_addon: AnkiSession, requests_mock, monkeypatch
):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.constants import API_URL_BASE, ChangeTypes

    client = AnkiHubClient(hooks=[])
    # test create new note suggestion
    deck_id = str(uuid.uuid4())
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=201
    )
    response = client.create_new_note_suggestion(
        ankihub_deck_uuid=deck_id,
        anki_id=1,
        ankihub_note_uuid=str(1),
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CARD_TO_ADD,
        note_type="Basic",
        note_type_id=1,
        comment="",
    )
    assert response.status_code == 201

    # test create new note suggestion unauthenticated
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=403
    )
    response = client.create_new_note_suggestion(
        ankihub_deck_uuid=deck_id,
        ankihub_note_uuid=str(1),
        anki_id=1,
        fields=[{"name": "abc", "order": 0, "value": "abc changed"}],
        tags=["test"],
        change_type=ChangeTypes.NEW_CARD_TO_ADD,
        note_type="Basic",
        note_type_id=1,
        comment="",
    )
    assert response.status_code == 403


def test_adjust_note_types(anki_session_with_addon: AnkiSession):
    from ankihub.utils import adjust_note_types, modify_note_type, sync_on_profile_open

    gui_hooks.profile_did_open.remove(sync_on_profile_open)
    anki_session = anki_session_with_addon
    with anki_session.profile_loaded():
        mw = anki_session.mw

        # for testing creating missing note type
        ankihub_basic_1 = copy.deepcopy(mw.col.models.by_name("Basic"))
        ankihub_basic_1["id"] = 1
        ankihub_basic_1["name"] = "AnkiHub Basic 1"
        modify_note_type(ankihub_basic_1)

        # for testing updating existing note type
        ankihub_basic_2 = copy.deepcopy(mw.col.models.by_name("Basic"))
        ankihub_basic_2["name"] = "AnkiHub Basic 2"
        modify_note_type(ankihub_basic_2)
        # ... save the note type
        ankihub_basic_2["id"] = 0
        changes = mw.col.models.add_dict(ankihub_basic_2)
        ankihub_basic_2["id"] = changes.id
        # ... then add a field
        new_field = mw.col.models.new_field("foo")
        new_field["ord"] = 2
        mw.col.models.add_field(ankihub_basic_2, new_field)

        remote_note_types = {
            ankihub_basic_1["id"]: ankihub_basic_1,
            ankihub_basic_2["id"]: ankihub_basic_2,
        }
        adjust_note_types(remote_note_types)

        assert mw.col.models.by_name("AnkiHub Basic 1") is not None
        assert mw.col.models.get(ankihub_basic_2["id"])["flds"][3]["name"] == "foo"
