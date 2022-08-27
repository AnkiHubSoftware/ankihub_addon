import pytest

pytestmark = pytest.mark.usefixtures("mw_mock")


@pytest.mark.vcr()
def test_client_login_and_signout(monkeypatch):
    monkeypatch.setenv("ANKIHUB_APP_URL", "http://localhost:8000")
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient

    client = AnkiHubClient()
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert len(token) == 64
    assert client.session.headers["Authorization"] == f"Token {token}"

    client.signout()
    assert client.session.headers["Authorization"] == ""
