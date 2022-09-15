from unittest.mock import MagicMock
import uuid

import pytest
import requests_mock


pytestmark = pytest.mark.usefixtures("mw_mock")


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
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(authorized_client, "get_presigned_url", get_presigned_url)
    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri("GET", get_presigned_url.return_value, content=b"a,b\n1,2")
        notes_data = authorized_client.download_deck(ankihub_deck_uuid=deck_id)
    assert notes_data
