from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ankihub.ankihub_client import AnkiHubClient

FAKE_BASE_URL = "http://fakeurl.com/api"


@patch("ankihub.config.Config")
def test_login(mocked_config, requests_mock):
    pass
    # credentials_data = {"username": "test", "password": "testpassword"}
    # mocked_config.get_base_url.return_value = FAKE_BASE_URL

    # requests_mock.post(f"{FAKE_BASE_URL}/login/", json={"token": "f4k3t0k3n"})
    # client = AnkiHubClient(mocked_config)
    # client.login(credentials=credentials_data)
    # assert client._headers["Authorization"] == "Token f4k3t0k3n"


# @patch("ankihub.config.Config")
# def test_signout(mocked_config):
#     client = AnkiHubClient(mocked_config)
#     client.signout()
#     mocked_config.save_token.assert_called_with("")
#     assert client._headers["Authorization"] == ""


# @patch("ankihub.config.Config")
# def test_upload_deck(mocked_config, requests_mock):
#     mocked_config.get_base_url.return_value = FAKE_BASE_URL
#     requests_mock.post(f"{FAKE_BASE_URL}/decks/", status_code=201)
#     client = AnkiHubClient(mocked_config)
#     response = client.upload_deck("test.apkg")
#     assert response.status_code == 201


# @patch("ankihub.config.Config")
# def test_get_deck_updates(mocked_config, requests_mock):
#     deck_id = 1
#     date_object = datetime.now(tz=timezone.utc) - timedelta(days=30)
#     date_time_str = datetime.strftime(date_object, "%Y-%m-%dT%H:%M:%S.%f%z")

#     expected_data = {
#         "since": date_object.timestamp(),
#         "notes": {
#             "deck_id": deck_id,
#             "note_id": 1,
#             "anki_id": 1,
#             "tags": ["New Tag"],
#             "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
#         },
#     }

#     mocked_config.get_base_url.return_value = FAKE_BASE_URL
#     mocked_config.get_last_sync.return_value = date_time_str
#     requests_mock.get(f"{FAKE_BASE_URL}/decks/{deck_id}/updates", json=expected_data)

#     client = AnkiHubClient(mocked_config)
#     response = client.get_deck_updates(deck_id=deck_id)
#     mocked_config.save_last_sync.assert_called_once()
#     assert response == expected_data


# @patch("ankihub.config.Config")
# def test_get_note_by_anki_id(mocked_config, requests_mock):
#     note_anki_id = 1
#     expected_data = {
#         "deck_id": 1,
#         "note_id": 1,
#         "anki_id": 1,
#         "tags": ["New Tag"],
#         "fields": [{"name": "Text", "order": 0, "value": "Fake value"}],
#     }
#     mocked_config.get_base_url.return_value = FAKE_BASE_URL
#     requests_mock.get(f"{FAKE_BASE_URL}/notes/{note_anki_id}", json=expected_data)
#     client = AnkiHubClient(mocked_config)
#     response = client.get_note_by_anki_id(anki_id=note_anki_id)
#     assert response == expected_data


# @patch("ankihub.config.Config")
# def test_create_note_suggestion(mocked_config, requests_mock):
#     mocked_config.get_base_url.return_value = FAKE_BASE_URL
#     note_id = 1
#     expected_data = {
#         "related_note": note_id,
#         "author": 1,
#         "tags": ["test"],
#         "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
#     }
#     requests_mock.post(
#         f"{FAKE_BASE_URL}/notes/{note_id}/suggestion/", json=expected_data
#     )
#     client = AnkiHubClient(mocked_config)
#     response = client.create_note_suggestion(
#         {
#             "tags": ["test"],
#             "fields": [{"name": "abc", "order": 0, "value": "abc changed"}],
#         },
#         note_id=note_id,
#     )
#     assert response == expected_data
