from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from pytest_anki import AnkiSession
from requests.exceptions import HTTPError

from ankihub.constants import API_URL_BASE


def test_login(anki_session_with_addon, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    credentials_data = {"username": "test", "password": "testpassword"}

    requests_mock.post(f"{API_URL_BASE}/login/", json={"token": "f4k3t0k3n"})
    client = AnkiHubClient()
    client.login(credentials=credentials_data)
    assert client._headers["Authorization"] == "Token f4k3t0k3n"


def test_signout(anki_session_with_addon: AnkiSession):
    from ankihub.ankihub_client import AnkiHubClient

    client = AnkiHubClient()
    client.signout()
    assert client._headers["Authorization"] == ""


def test_upload_deck(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=201)
    client = AnkiHubClient()
    response = client.upload_deck("test.apkg")
    assert response.status_code == 201


def test_upload_deck_unauthenticated(
        anki_session_with_addon: AnkiSession,
        requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient

    requests_mock.post(f"{API_URL_BASE}/decks/", status_code=403)
    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.upload_deck("test.apkg")


def test_get_deck_updates(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    deck_id = 1
    date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)
    date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")

    expected_data = {
        "since": date_object.timestamp(),
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

    client = AnkiHubClient()
    response = client.get_deck_updates(deck_id=deck_id)
    assert response == expected_data


def test_get_deck_updates_unauthenticated(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    deck_id = 1
    date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)
    date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/updates", status_code=403)

    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.get_deck_updates(deck_id=deck_id)


def test_get_deck_by_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

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
    client = AnkiHubClient()
    response = client.get_deck_by_id(deck_id=deck_id)
    assert response == expected_data


def test_get_deck_by_id_unauthenticated(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    deck_id = 1

    requests_mock.get(f"{API_URL_BASE}/decks/{deck_id}/", status_code=403)

    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.get_deck_by_id(deck_id=deck_id)


def test_get_note_by_anki_id(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    note_anki_id = 1
    expected_data = {
        "deck_id": 1,
        "note_id": 1,
        "anki_id": 1,
        "tags": ["New Tag"],
        "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
    }
    requests_mock.get(f"{API_URL_BASE}/notes/{note_anki_id}", json=expected_data)
    client = AnkiHubClient()
    response = client.get_note_by_anki_id(anki_id=note_anki_id)
    assert response == expected_data


def test_get_note_by_anki_id_unauthenticated(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    note_anki_id = 1

    requests_mock.get(f"{API_URL_BASE}/notes/{note_anki_id}", status_code=403)
    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.get_note_by_anki_id(anki_id=note_anki_id)


def test_create_change_note_suggestion(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    note_id = 1
    requests_mock.post(
        f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=201
    )
    client = AnkiHubClient()
    response = client.create_change_note_suggestion(
        {
            "tags": ["test"],
            "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
        },
        note_id=note_id,
    )
    assert response.status_code == 201


def test_create_change_note_suggestion_unauthenticated(
        anki_session_with_addon: AnkiSession,
        requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient

    note_id = 1
    requests_mock.post(f"{API_URL_BASE}/notes/{note_id}/suggestion/", status_code=403)
    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.create_change_note_suggestion(
            {
                "tags": ["test"],
                "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
            },
            note_id=note_id,
        )


def test_create_new_note_suggestion(anki_session_with_addon: AnkiSession, requests_mock):
    from ankihub.ankihub_client import AnkiHubClient

    deck_id = 1
    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=201
    )
    client = AnkiHubClient()
    response = client.create_new_note_suggestion(
        {
            "tags": ["test"],
            "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
        },
        deck_id=deck_id,
    )
    assert response.status_code == 201


def test_create_new_note_suggestion_unauthenticated(
        anki_session_with_addon: AnkiSession,
        requests_mock
):
    from ankihub.ankihub_client import AnkiHubClient

    deck_id = 1

    requests_mock.post(
        f"{API_URL_BASE}/decks/{deck_id}/note-suggestion/", status_code=403
    )
    with pytest.raises(HTTPError):
        client = AnkiHubClient()
        client.create_new_note_suggestion(
            {
                "tags": ["test"],
                "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
            },
            deck_id=deck_id,
        )
