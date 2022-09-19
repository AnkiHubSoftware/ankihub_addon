import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests_mock

DECK_CSV_PATH = Path("tests/test_data/deck_with_one_basic_note.csv")

pytestmark = [pytest.mark.usefixtures("mw_mock"), pytest.mark.client]


@pytest.fixture(autouse=True)
def set_ankihub_app_url(monkeypatch):
    monkeypatch.setenv("ANKIHUB_APP_URL", "http://localhost:8000")


@pytest.fixture
def client():
    from ankihub.ankihub_client import AnkiHubClient

    client = AnkiHubClient()
    yield client


@pytest.fixture
def authorized_client():
    from ankihub.ankihub_client import AnkiHubClient

    client = AnkiHubClient()
    credentials_data = {"username": "test1", "password": "asdf"}
    client.login(credentials=credentials_data)
    yield client


@pytest.mark.vcr()
def test_client_login_and_signout(client):
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert len(token) == 64
    assert client.session.headers["Authorization"] == f"Token {token}"

    client.signout()
    assert client.session.headers["Authorization"] == ""


@pytest.mark.vcr()
def test_download_deck(authorized_client, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient

    client: AnkiHubClient = authorized_client
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(client, "get_presigned_url", get_presigned_url)
    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri(
            "GET", get_presigned_url.return_value, content=DECK_CSV_PATH.read_bytes()
        )
        notes_data = client.download_deck(ankihub_deck_uuid=deck_id)
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_download_deck_with_progress(authorized_client, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient
    from ankihub.gui.decks import download_progress_cb

    client: AnkiHubClient = authorized_client
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(client, "get_presigned_url", get_presigned_url)
    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri(
            "GET",
            get_presigned_url.return_value,
            content=DECK_CSV_PATH.read_bytes(),
            headers={"content-length": "1000000"},
        )
        notes_data = client.download_deck(
            ankihub_deck_uuid=deck_id, download_progress_cb=download_progress_cb
        )
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_create_suggestions_in_bulk(authorized_client):
    from ankihub.ankihub_client import (
        AnkiHubClient,
        ChangeNoteSuggestion,
        Field,
        NewNoteSuggestion,
        SuggestionType,
    )

    # the uuids have to be the same in each run to make testing with vcr casettes work
    UUID_1 = uuid.UUID("1da0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    UUID_2 = uuid.UUID("2da0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    UUID_3 = uuid.UUID("3da0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    UUID_4 = uuid.UUID("4da0d3ad-89cd-45fb-8ddc-fabad93c2d7b")

    client: AnkiHubClient = authorized_client
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")

    new_note_suggestion = NewNoteSuggestion(
        ankihub_note_uuid=UUID_1,
        anki_note_id=1,
        fields=[
            Field(name="Front", value="front1", order=0),
            Field(name="Back", value="back1", order=1),
        ],
        tags=["tag1", "tag2"],
        comment="comment1",
        ankihub_deck_uuid=deck_id,
        note_type_name="Basic",
        anki_note_type_id=1,
    )

    # create a new note suggestion
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[new_note_suggestion], auto_accept=False
    )
    assert errors_by_nid == {}

    # create two new note suggestions at once
    new_note_suggestion.ankihub_note_uuid = UUID_2
    new_note_suggestion_2 = deepcopy(new_note_suggestion)
    new_note_suggestion_2.ankihub_note_uuid = UUID_3
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[new_note_suggestion, new_note_suggestion_2], auto_accept=False
    )
    assert errors_by_nid == {}

    # try creating a new note suggestion with the same ankihub_note_uuid as the first one
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[new_note_suggestion], auto_accept=False
    )
    assert len(
        errors_by_nid
    ) == 1 and "new note suggestion with this ankihub id already exists." in str(
        errors_by_nid
    )

    ankihub_note_uuid = UUID_4

    # create an auto-accepted new note suggestion and check if note was created
    new_note_suggestion.ankihub_note_uuid = ankihub_note_uuid
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[new_note_suggestion], auto_accept=True
    )
    note = client.get_note_by_id(ankihub_note_uuid=ankihub_note_uuid)
    assert note.fields == new_note_suggestion.fields
    assert note.tags == new_note_suggestion.tags

    change_note_suggestion = ChangeNoteSuggestion(
        ankihub_note_uuid=ankihub_note_uuid,
        anki_note_id=1,
        fields=[
            Field(name="Front", value="front2", order=0),
            Field(name="Back", value="back2", order=1),
        ],
        tags=["tag3", "tag4"],
        comment="comment1",
        change_type=SuggestionType.UPDATED_CONTENT,
    )

    # create a change note suggestion
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[change_note_suggestion], auto_accept=False
    )
    assert errors_by_nid == {}

    # create an auto-accepted change note suggestion and assert that note was changed
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[change_note_suggestion], auto_accept=True
    )
    note = client.get_note_by_id(ankihub_note_uuid=ankihub_note_uuid)
    assert errors_by_nid == {}
    assert note.fields == change_note_suggestion.fields
    assert note.tags == change_note_suggestion.tags

    # create a change note suggestion without any changes
    errors_by_nid = client.create_suggestions_in_bulk(
        suggestions=[change_note_suggestion], auto_accept=False
    )
    assert len(
        errors_by_nid
    ) == 1 and "Suggestion fields and tags don't have any changes to the original note" in str(
        errors_by_nid
    )
