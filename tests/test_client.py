import pytest

pytestmark = pytest.mark.usefixtures("mw_mock")


@pytest.mark.vcr()
def test_client_login_and_signout(monkeypatch):
    monkeypatch.setenv("ANKIHUB_APP_URL", "http://localhost:8000")
    from ankihub.addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient

    client = AnkiHubClient()
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert token == "8cbecaf7b452d603a043ea41a8467661def00fca619f335d9a18535d02c36ea2"
    assert (
        client.session.headers["Authorization"]
        == "Token 8cbecaf7b452d603a043ea41a8467661def00fca619f335d9a18535d02c36ea2"
    )

    # test signout
    client.signout()
    assert client.session.headers["Authorization"] == ""
