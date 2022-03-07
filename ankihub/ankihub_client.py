import json
import requests

from ankihub.config import Config


class AnkiHubClient:
    """Client for interacting with the AnkiHub API."""

    def __init__(self, config: Config = None):
        self._headers = {"Content-Type": "application/json"}
        self._config = config if config else Config()
        self._base_url = self._config.get_base_url()
        if self._config.get_token():
            token = self._config.get_token()
            self._headers["Authorization"] = f"Token {token}"

    def _is_authenticated(self) -> bool:
        return "Authorization" in self._headers and self._headers != ""

    def _call_api(self, method, endpoint, data=None, params=None):
        response = requests.request(
            method=method,
            headers=self._headers,
            url=f"{self._base_url}{endpoint}",
            json=data,
            params=params,
        )
        response.raise_for_status()
        return response

    def _call_api_authenticated(self, *args, **kwargs):
        if self._is_authenticated():
            return self._call_api(*args, **kwargs)
        else:
            raise Exception

    def login(self, credentials: dict):
        response = self._call_api("POST", "/login/", credentials)
        token = response.json()["token"]
        if self._config:
            self._config.save_token(token)
        self._headers["Authorization"] = f"Token {token}"

    def signout(self):
        self._config.save_token("")
        self._headers["Authorization"] = ""

    def upload_deck(self, key: str):
        response = self._call_api_authenticated("POST", "/decks/", data={"key": key})
        return response

    def get_deck_updates(self, deck_id: str) -> dict:
        response = self._call_api_authenticated(
            "GET",
            f"/decks/{deck_id}/updates",
            params={"since": f"{self._config.get_last_sync()}"},
        )
        self._config.save_last_sync()
        return response.json()

    def get_note_by_anki_id(self, anki_id: str) -> dict:
        return self._call_api_authenticated("GET", f"/notes/{anki_id}").json()

    def create_note_suggestion(self, note_suggestion: dict, note_id: int) -> dict:
        return self._call_api_authenticated(
            "POST", f"/notes/{note_id}/suggestion/", note_suggestion
        ).json()
        
    # legacy methods
    
    def authenticate_user(self, url: str, data: dict) -> str:
        """Authenticate the user and return their token."""
        token = ""
        response = requests.post(
            self._base_url + url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(data),
        )
        if response.status_code == 200:
            token = json.loads(response.content)["token"]
            self._config.save_token(token)
        return token

    def post_apkg(self, url, data, file):
        headers = {"Authorization": "Token " + self._config.get_token()}
        return requests.post(
            self._base_url + url,
            headers=headers,
            files={"file": open(file, "rb")},
            data=data,
        )

    def post(self, url, data):
        return requests.post(
            self._base_url + url, headers=self._headers, data=json.dumps(data)
        )

    def get(self, url):
        return requests.get(self._base_url + url, headers=self._headers)

    def submit_change(self):
        print("Submitting change")

    def submit_new_note(self):
        print("Submitting new note")
