import pytest

pytestmark = pytest.mark.usefixtures("mw_mock")


@pytest.mark.vcr()
def test_client_login_and_signout(monkeypatch):
    monkeypatch.setenv("ANKIHUB_APP_URL", "http://localhost:8000")
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient

    client = AnkiHubClient()
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert token == "f4k3t0k3n"
    assert client.session.headers["Authorization"] == "Token f4k3t0k3n"

    # test signout
    client.signout()
    assert client.session.headers["Authorization"] == ""
