import uuid
from ankihub.ankihub_client import AnkiHubClient

import pytest


pytestmark = pytest.mark.usefixtures("mw_mock")


@pytest.fixture(autouse=True)
def set_ankihub_app_url(monkeypatch):
    monkeypatch.setenv("ANKIHUB_APP_URL", "http://localhost:8000")


@pytest.fixture
def client():
    client = AnkiHubClient()
    yield client


@pytest.fixture
def authorized_client():
    client = AnkiHubClient()
    credentials_data = {"username": "test1", "password": "asdf"}
    client.login(credentials=credentials_data)
    yield client


@pytest.mark.vcr()
def test_client_login_and_signout(client: AnkiHubClient):
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert len(token) == 64
    assert client.session.headers["Authorization"] == f"Token {token}"

    client.signout()
    assert client.session.headers["Authorization"] == ""


@pytest.mark.vcr()
def test_download_deck(authorized_client: AnkiHubClient):
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
    notes_data = authorized_client.download_deck(ankihub_deck_uuid=deck_id)
    assert notes_data
