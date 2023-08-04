import gzip
import json
import os
import subprocess
import tempfile
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List
from unittest.mock import MagicMock, Mock

import pytest
import requests_mock
from pytest import FixtureRequest, MonkeyPatch
from requests_mock import Mocker
from vcr import VCR  # type: ignore

from ..factories import NoteInfoFactory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import (
    DEFAULT_API_URL,
    DEFAULT_S3_BUCKET_URL,
    AnkiHubClient,
    AnkiHubHTTPError,
    ChangeNoteSuggestion,
    Deck,
    DeckExtension,
    Field,
    NewNoteSuggestion,
    NoteCustomization,
    NoteInfo,
    NoteSuggestion,
    OptionalTagSuggestion,
    SuggestionType,
    TagGroupValidationResponse,
    UserDeckRelation,
    get_media_names_from_notes_data,
    get_media_names_from_suggestion,
)
from ankihub.ankihub_client.ankihub_client import (
    DeckExtensionUpdateChunk,
    DeckUpdateChunk,
)
from ankihub.gui.operations.deck_installation import _download_progress_cb

COMPOSE_FILE = Path(os.getenv("COMPOSE_FILE")) if os.getenv("COMPOSE_FILE") else None

TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"
DECK_CSV = TEST_DATA_PATH / "deck_with_one_basic_note.csv"
DECK_CSV_GZ = TEST_DATA_PATH / "deck_with_one_basic_note.csv.gz"

TEST_MEDIA_PATH = TEST_DATA_PATH / "media"

VCR_CASSETTES_PATH = Path(__file__).parent / "cassettes"


# defined in create_fixture_data.py script in django app
DECK_WITH_EXTENSION_UUID = uuid.UUID("100df7b9-7749-4fe0-b801-e3dec1decd72")
DECK_EXTENSION_ID = 999

LOCAL_API_URL = "http://localhost:8000/api"


ID_OF_DECK_OF_USER_TEST1 = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
ID_OF_DECK_OF_USER_TEST2 = uuid.UUID("5528aef7-f7ac-406b-9b35-4eaf00de4b20")


@pytest.fixture
def client_with_server_setup(vcr: VCR, request, marks):
    if "skipifvcr" in marks and vcr_enabled(vcr):
        pytest.skip("Skipping test because test has skipifvcr mark and VCR is enabled")

    cassette_name = ".".join(request.node.nodeid.split("::")[1:]) + ".yaml"
    cassette_path = VCR_CASSETTES_PATH / cassette_name
    playback_mode = vcr_enabled(vcr) and cassette_path.exists()

    if not playback_mode:
        run_command_in_django_container(
            "python manage.py flush --no-input && "
            "python manage.py runscript create_test_users && "
            "python manage.py runscript create_fixture_data"
        )

    client = AnkiHubClient(api_url=LOCAL_API_URL, local_media_dir_path=TEST_MEDIA_PATH)
    yield client


def run_command_in_django_container(command):
    result = subprocess.run(
        [
            "sudo",
            "docker-compose",
            "-f",
            COMPOSE_FILE.absolute(),
            "run",
            "--rm",
            "django",
            "bash",
            "-c",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        print(f"Command '{command}' failed with error code {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
    else:
        print(f"Command '{command}' executed successfully.")
        print(f"Stdout: {result.stdout}")

    return result


@pytest.fixture
def marks(request):
    # Yields a list of all marks on the current test
    # See https://stackoverflow.com/a/61379477/6827339
    marks = [m.name for m in request.node.iter_markers()]
    if request.node.parent:
        marks += [m.name for m in request.node.parent.iter_markers()]
    yield marks


def vcr_enabled(vcr: VCR):
    # See https://github.com/ktosiek/pytest-vcr/blob/master/pytest_vcr.py#L59-L62
    return not (
        vcr.record_mode == "new_episodes"
        and vcr.before_record_response
        and vcr.before_record_response() is None
    )


@pytest.fixture
def authorized_client_for_user_test1(client_with_server_setup: AnkiHubClient):
    credentials_data = {"username": "test1", "password": "asdf"}
    client_with_server_setup.login(credentials=credentials_data)
    yield client_with_server_setup


@pytest.fixture
def authorized_client_for_user_test2(client_with_server_setup: AnkiHubClient):
    credentials_data = {"username": "test2", "password": "asdf"}
    client_with_server_setup.login(credentials=credentials_data)
    yield client_with_server_setup


@pytest.fixture
def unauthorized_client(client_with_server_setup: AnkiHubClient):
    """Client that is not logged in. Yields the same client as client_with_server_setup fixture,
    but the name is more descriptive."""
    yield client_with_server_setup


@pytest.fixture
def new_note_suggestion(
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    ah_nid = next_deterministic_uuid()
    return NewNoteSuggestion(
        ah_nid=ah_nid,
        anki_nid=1,
        fields=[
            Field(name="Front", value="front1", order=0),
            Field(name="Back", value="back1", order=1),
        ],
        tags=["tag1", "tag2"],
        guid="asdf",
        comment="comment1",
        ah_did=ah_nid,
        note_type_name="Cloze",
        anki_note_type_id=1,
    )


@pytest.fixture
def new_note_suggestion_note_info(
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    return NoteInfo(
        ah_nid=next_deterministic_uuid(),
        anki_nid=1,
        fields=[
            Field(name="Front", value="front1", order=0),
            Field(name="Back", value="back1", order=1),
        ],
        tags=["tag1", "tag2"],
        mid=1,
        last_update_type=None,
        guid="asdf",
    )


@pytest.fixture
def change_note_suggestion(
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    return ChangeNoteSuggestion(
        ah_nid=next_deterministic_uuid(),
        anki_nid=1,
        fields=[
            Field(name="Front", value="front2", order=0),
            Field(name="Back", value="back2", order=1),
        ],
        added_tags=["tag3", "tag4"],
        removed_tags=[],
        comment="comment1",
        change_type=SuggestionType.UPDATED_CONTENT,
    )


@pytest.fixture
def remove_generated_media_files():
    _remove_generated_media_files()
    yield
    _remove_generated_media_files()


def _remove_generated_media_files():
    for file in TEST_MEDIA_PATH.glob("*"):
        if not file.is_file():
            continue
        if not file.name.lower().startswith("testfile_"):
            file.unlink()


@pytest.mark.vcr()
def test_client_login_and_signout_with_username(client_with_server_setup):
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client_with_server_setup.login(credentials=credentials_data)
    assert len(token) == 64
    assert client_with_server_setup.token == token

    client_with_server_setup.signout()
    assert client_with_server_setup.token is None


@pytest.mark.vcr()
def test_client_login_and_signout_with_email(client_with_server_setup):
    credentials_data = {"email": "test1@email.com", "password": "asdf"}
    token = client_with_server_setup.login(credentials=credentials_data)
    assert len(token) == 64
    assert client_with_server_setup.token == token

    client_with_server_setup.signout()
    assert client_with_server_setup.token is None


@pytest.mark.vcr()
def test_download_deck(
    authorized_client_for_user_test1: AnkiHubClient, monkeypatch: MonkeyPatch
):
    client = authorized_client_for_user_test1

    get_presigned_url_suffix = MagicMock()
    get_presigned_url_suffix.return_value = "/fake_key"
    monkeypatch.setattr(client, "_get_presigned_url_suffix", get_presigned_url_suffix)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.get(
            f"{client.s3_bucket_url}{get_presigned_url_suffix.return_value}",
            content=DECK_CSV.read_bytes(),
        )
        notes_data = client.download_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_download_compressed_deck(
    authorized_client_for_user_test1: AnkiHubClient, monkeypatch: MonkeyPatch
):
    client = authorized_client_for_user_test1

    get_presigned_url_suffix = MagicMock()
    get_presigned_url_suffix.return_value = "/fake_key"
    monkeypatch.setattr(client, "_get_presigned_url_suffix", get_presigned_url_suffix)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv.gz"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.get(
            f"{client.s3_bucket_url}{get_presigned_url_suffix.return_value}",
            content=DECK_CSV_GZ.read_bytes(),
        )
        notes_data = client.download_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_download_deck_with_progress(
    authorized_client_for_user_test1: AnkiHubClient, monkeypatch: MonkeyPatch
):
    client = authorized_client_for_user_test1

    get_presigned_url_suffix = MagicMock()
    get_presigned_url_suffix.return_value = "/fake_key"
    monkeypatch.setattr(client, "_get_presigned_url_suffix", get_presigned_url_suffix)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.get(
            f"{client.s3_bucket_url}{get_presigned_url_suffix.return_value}",
            content=DECK_CSV.read_bytes(),
            headers={"content-length": "1000000"},
        )
        notes_data = client.download_deck(
            ah_did=ID_OF_DECK_OF_USER_TEST1,
            download_progress_cb=_download_progress_cb,
        )
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


def create_note_on_ankihub_and_assert(
    client, new_note_suggestion, uuid_of_deck: uuid.UUID
):
    # utility function meant to be used in tests for creating a note with known values on ankihub
    # asserts that the note was created correctly
    assert isinstance(client, AnkiHubClient)
    assert isinstance(new_note_suggestion, NewNoteSuggestion)

    # create an auto-accepted new note suggestion
    new_note_suggestion.ah_did = uuid_of_deck
    errors_by_nid = client.create_suggestions_in_bulk(
        new_note_suggestions=[new_note_suggestion], auto_accept=True
    )
    assert errors_by_nid == {}

    # assert that note was created
    note = client.get_note_by_id(ah_nid=new_note_suggestion.ah_nid)
    assert note.fields == new_note_suggestion.fields
    assert set(note.tags) == set(new_note_suggestion.tags)


@pytest.mark.vcr()
def test_upload_deck(
    authorized_client_for_user_test1: AnkiHubClient,
    next_deterministic_id: Callable[[], int],
    monkeypatch: MonkeyPatch,
):
    client = authorized_client_for_user_test1

    note_data = NoteInfoFactory.create()

    # create the deck on AnkiHub
    # upload to s3 is mocked out, this will potentially cause errors on the locally running AnkiHub
    # because the deck will not be uploaded to s3, but we don't care about that here
    upload_to_s3_mock = Mock()
    with monkeypatch.context() as m:
        m.setattr(client, "_upload_to_s3", upload_to_s3_mock)
        m.setattr(
            client, "_get_presigned_url_suffix", lambda *args, **kwargs: "fake_key"
        )

        client.upload_deck(
            deck_name="test deck",
            notes_data=[note_data],
            note_types_data=[],
            anki_deck_id=next_deterministic_id(),
            private=False,
        )

    # check that the deck would be uploaded to s3
    assert upload_to_s3_mock.call_count == 1
    payload = json.loads(
        gzip.decompress(upload_to_s3_mock.call_args[0][1]).decode("utf-8")
    )
    assert len(payload["notes"]) == 1
    note_from_payload = payload["notes"][0]
    note_from_payload["note_id"] = note_from_payload["id"]
    note_from_payload.pop("id")

    note_data_dict = note_data.to_dict()
    note_data_dict.pop("last_update_type")

    assert note_from_payload == note_data_dict


class TestCreateSuggestion:
    @pytest.mark.vcr()
    def test_create_change_note_suggestion_without_all_fields(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        change_note_suggestion: ChangeNoteSuggestion,
    ):
        client = authorized_client_for_user_test1

        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            ID_OF_DECK_OF_USER_TEST1,
        )

        # create a change note suggestion without all fields (for the same note)
        cns: ChangeNoteSuggestion = change_note_suggestion
        cns.ah_nid = new_note_suggestion.ah_nid
        cns.fields = [
            Field(name="Front", value="front2", order=0),
        ]

        # ... this shouldn't raise an exception
        client.create_change_note_suggestion(
            change_note_suggestion=cns,
            auto_accept=True,
        )


class TestCreateSuggestionsInBulk:
    @pytest.mark.vcr()
    def test_create_one_new_note_suggestion(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
    ):
        client = authorized_client_for_user_test1

        new_note_suggestion.ah_did = ID_OF_DECK_OF_USER_TEST1
        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion],
            auto_accept=False,
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_create_two_new_note_suggestions(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        client = authorized_client_for_user_test1

        # create two new note suggestions at once
        new_note_suggestion.ah_did = ID_OF_DECK_OF_USER_TEST1

        new_note_suggestion_2 = deepcopy(new_note_suggestion)
        new_note_suggestion_2.ah_nid = next_deterministic_uuid()
        new_note_suggestion_2.anki_nid = 2

        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion, new_note_suggestion_2],
            auto_accept=False,
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_use_same_ankihub_id_for_new_note_suggestion(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
    ):
        client = authorized_client_for_user_test1

        # create a new note suggestion
        new_note_suggestion.ah_did = ID_OF_DECK_OF_USER_TEST1
        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion], auto_accept=False
        )
        assert errors_by_nid == {}

        # try creating a new note suggestion with the same ah_nid as the first one
        new_note_suggestion_2 = deepcopy(new_note_suggestion)
        new_note_suggestion_2.anki_nid = 2
        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion], auto_accept=False
        )
        assert len(
            errors_by_nid
        ) == 1 and "Suggestion with this id already exists" in str(errors_by_nid)

    @pytest.mark.vcr()
    def test_create_auto_accepted_new_note_suggestion(
        self,
        authorized_client_for_user_test1,
        new_note_suggestion,
    ):
        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            ID_OF_DECK_OF_USER_TEST1,
        )

    @pytest.mark.vcr()
    def test_create_change_note_suggestion(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        change_note_suggestion: ChangeNoteSuggestion,
    ):
        client = authorized_client_for_user_test1

        new_note_suggestion.ah_did = ID_OF_DECK_OF_USER_TEST1
        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            ID_OF_DECK_OF_USER_TEST1,
        )

        # create a change note suggestion
        change_note_suggestion.ah_nid = new_note_suggestion.ah_nid
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=False
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_create_auto_accepted_change_note_suggestion(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        change_note_suggestion: ChangeNoteSuggestion,
    ):
        client = authorized_client_for_user_test1

        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            ID_OF_DECK_OF_USER_TEST1,
        )

        # create an auto-accepted change note suggestion and assert that note was changed
        change_note_suggestion.ah_nid = new_note_suggestion.ah_nid
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=True
        )
        note = client.get_note_by_id(ah_nid=new_note_suggestion.ah_nid)
        assert errors_by_nid == {}
        assert note.fields == change_note_suggestion.fields
        assert set(note.tags) == set(change_note_suggestion.added_tags) | set(
            new_note_suggestion.tags
        )

        # create a change note suggestion without any changes
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=False
        )
        assert len(
            errors_by_nid
        ) == 1 and "Suggestion fields and tags don't have any changes to the original note" in str(
            errors_by_nid
        )


class TestDeckSubscriptions:
    @pytest.mark.vcr()
    def test_get_empty_subscriptions(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        assert client.get_deck_subscriptions() == []

    @pytest.mark.vcr()
    def test_get_deck_subscriptions_with_unauthorized_client(
        self,
        unauthorized_client: AnkiHubClient,
    ):
        client = unauthorized_client
        try:
            client.get_deck_subscriptions()
        except AnkiHubHTTPError:
            pass
        else:
            assert False, "AnkiHubHTTPError was not raised"

    @pytest.mark.vcr()
    def test_subscribe_and_get_list_of_subscriptions(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        assert client.get_deck_subscriptions() == []

        client.subscribe_to_deck(ID_OF_DECK_OF_USER_TEST1)

        decks = client.get_deck_subscriptions()
        assert len(decks) == 1
        deck: Deck = decks[0]
        assert deck.ah_did == ID_OF_DECK_OF_USER_TEST1

    @pytest.mark.vcr()
    def test_subscribe_with_unauthorized_client(
        self,
        unauthorized_client: AnkiHubClient,
    ):
        client = unauthorized_client
        try:
            client.subscribe_to_deck(ID_OF_DECK_OF_USER_TEST1)
        except AnkiHubHTTPError:
            pass
        else:
            assert False, "AnkiHubHTTPError was not raised"

    @pytest.mark.vcr()
    def test_subscribe_and_unsubscribe(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        assert client.get_deck_subscriptions() == []

        client.subscribe_to_deck(ID_OF_DECK_OF_USER_TEST1)

        decks = client.get_deck_subscriptions()
        assert len(decks) == 1
        deck: Deck = decks[0]
        assert deck.ah_did == ID_OF_DECK_OF_USER_TEST1

        client.unsubscribe_from_deck(ID_OF_DECK_OF_USER_TEST1)
        assert client.get_deck_subscriptions() == []

    @pytest.mark.vcr()
    def test_unsubscribe_from_deck_that_user_is_not_subscribed_to(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        client.unsubscribe_from_deck(ID_OF_DECK_OF_USER_TEST1)

    @pytest.mark.vcr()
    def test_unsubscribe_with_unauthorized_client(
        self,
        unauthorized_client: AnkiHubClient,
    ):
        client = unauthorized_client
        try:
            client.unsubscribe_from_deck(ID_OF_DECK_OF_USER_TEST1)
        except AnkiHubHTTPError:
            pass
        else:
            assert False, "AnkiHubHTTPError was not raised"


class TestDecksWithUserRelation:
    @pytest.mark.vcr()
    def test_subscribe_and_get_list_of_decks_with_user_relation(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1

        decks = client.get_decks_with_user_relation()
        assert len(decks) == 1
        deck: Deck = decks[0]
        assert deck.ah_did == ID_OF_DECK_OF_USER_TEST1
        assert deck.user_relation == UserDeckRelation.OWNER

        client.subscribe_to_deck(ID_OF_DECK_OF_USER_TEST2)

        decks = client.get_decks_with_user_relation()
        assert len(decks) == 2
        assert set(deck.ah_did for deck in decks) == set(
            [
                ID_OF_DECK_OF_USER_TEST1,
                ID_OF_DECK_OF_USER_TEST2,
            ]
        )
        deck_of_user_test2 = next(
            deck for deck in decks if deck.ah_did == ID_OF_DECK_OF_USER_TEST2
        )
        assert deck_of_user_test2.user_relation == UserDeckRelation.SUBSCRIBER


class TestGetDeckUpdates:
    @pytest.mark.vcr()
    def test_get_deck_updates(
        self,
        authorized_client_for_user_test2: AnkiHubClient,
        monkeypatch: MonkeyPatch,
    ):
        client = authorized_client_for_user_test2

        page_size = 5
        monkeypatch.setattr(
            "ankihub.ankihub_client.ankihub_client.DECK_UPDATE_PAGE_SIZE", page_size
        )
        update_chunks: List[DeckUpdateChunk] = list(
            client.get_deck_updates(ID_OF_DECK_OF_USER_TEST2, since=None)
        )
        assert len(update_chunks) == 2
        assert all(len(chunk.notes) == page_size for chunk in update_chunks)

    @pytest.mark.skipifvcr()
    def test_get_deck_updates_since(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        new_note_suggestion_note_info: NoteInfo,
    ):
        client = authorized_client_for_user_test1

        since_time = datetime.now(timezone.utc)

        # create a new note
        new_note_suggestion.ah_did = ID_OF_DECK_OF_USER_TEST1
        client.create_new_note_suggestion(new_note_suggestion, auto_accept=True)

        # get deck updates since the time of the new note creation
        chunks = list(
            client.get_deck_updates(ah_did=ID_OF_DECK_OF_USER_TEST1, since=since_time)
        )

        note_info: NoteInfo = new_note_suggestion_note_info
        note_info.ah_nid = new_note_suggestion.ah_nid
        note_info.anki_nid = new_note_suggestion.anki_nid
        note_info.guid = new_note_suggestion.guid

        assert len(chunks) == 1
        assert chunks[0] == DeckUpdateChunk(
            latest_update=chunks[0].latest_update,  # not the same as since_time_str
            notes=[note_info],
            protected_fields={},
            protected_tags=[],
        )


@pytest.mark.vcr()
def test_get_deck_extensions_by_deck_id(
    authorized_client_for_user_test1: AnkiHubClient,
):
    client = authorized_client_for_user_test1

    response = client.get_deck_extensions_by_deck_id(deck_id=DECK_WITH_EXTENSION_UUID)
    assert response == [
        DeckExtension(
            id=999,
            owner_id=1,
            ah_did=DECK_WITH_EXTENSION_UUID,
            name="test100",
            tag_group_name="test100",
            description="",
        )
    ]


@pytest.mark.vcr()
def test_get_note_customizations_by_deck_extension_id(
    authorized_client_for_user_test1: AnkiHubClient,
):
    client = authorized_client_for_user_test1

    deck_extension_id = 999

    expected_response = DeckExtensionUpdateChunk(
        note_customizations=[
            NoteCustomization(
                ankihub_nid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
            NoteCustomization(
                ankihub_nid=uuid.UUID("b2344a94-0ca6-44a1-87a1-1593558c10a9"),
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
        ],
    )

    chunks = list(
        client.get_deck_extension_updates(
            deck_extension_id=deck_extension_id, since=None
        )
    )
    assert len(chunks) == 1
    chunk = chunks[0]

    expected_response.latest_update = chunk.latest_update
    assert chunk == expected_response


@pytest.mark.vcr()
def test_get_media_disabled_fields(
    authorized_client_for_user_test1: AnkiHubClient, monkeypatch: MonkeyPatch
):
    client = authorized_client_for_user_test1

    deck_uuid = ID_OF_DECK_OF_USER_TEST1

    response = client.get_media_disabled_fields(deck_uuid)

    expected_response = {1: ["Extra"], 32738523: ["Text", "Pixorize", "First Aid"]}

    assert response == expected_response


@pytest.mark.vcr()
def test_is_media_upload_finished_is_false(
    authorized_client_for_user_test1: AnkiHubClient,
):
    client = authorized_client_for_user_test1

    deck_uuid = ID_OF_DECK_OF_USER_TEST1

    assert not client.is_media_upload_finished(deck_uuid)


@pytest.mark.vcr()
def test_media_upload_finished(authorized_client_for_user_test1: AnkiHubClient):
    client = authorized_client_for_user_test1

    deck_uuid = ID_OF_DECK_OF_USER_TEST1

    assert not client.is_media_upload_finished(deck_uuid)

    client.media_upload_finished(deck_uuid)

    assert client.is_media_upload_finished(deck_uuid)


@pytest.mark.vcr()
def test_get_note_customizations_by_deck_extension_id_in_multiple_chunks(
    authorized_client_for_user_test1: AnkiHubClient, monkeypatch: MonkeyPatch
):
    client = authorized_client_for_user_test1

    deck_extension_id = 999

    monkeypatch.setattr(
        "ankihub.ankihub_client.ankihub_client.DECK_EXTENSION_UPDATE_PAGE_SIZE", 1
    )

    expected_chunk_1 = DeckExtensionUpdateChunk(
        note_customizations=[
            NoteCustomization(
                ankihub_nid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
        ]
    )

    expected_chunk_2 = DeckExtensionUpdateChunk(
        note_customizations=[
            NoteCustomization(
                ankihub_nid=uuid.UUID("b2344a94-0ca6-44a1-87a1-1593558c10a9"),
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
        ]
    )

    chunks = list(
        client.get_deck_extension_updates(
            deck_extension_id=deck_extension_id, since=None
        )
    )
    assert len(chunks) == 2
    chunk_1, chunk_2 = chunks

    expected_chunk_1.latest_update = chunk_1.latest_update
    expected_chunk_2.latest_update = chunk_2.latest_update
    assert chunk_1 == expected_chunk_1
    assert chunk_2 == expected_chunk_2


@pytest.mark.vcr()
def test_prevalidate_tag_groups(authorized_client_for_user_test2: AnkiHubClient):
    client = authorized_client_for_user_test2

    tag_group_validation_responses = client.prevalidate_tag_groups(
        ah_did=DECK_WITH_EXTENSION_UUID,
        tag_group_names=["test100", "invalid"],
    )
    assert tag_group_validation_responses == [
        TagGroupValidationResponse(
            tag_group_name="test100",
            deck_extension_id=DECK_EXTENSION_ID,
            success=True,
            errors=[],
        ),
        TagGroupValidationResponse(
            tag_group_name="invalid",
            deck_extension_id=None,
            success=False,
            errors=[
                "This Deck Extension does not exist. Please create one for this Deck on AnkiHub."
            ],
        ),
    ]


@pytest.mark.vcr()
def test_suggest_optional_tags(authorized_client_for_user_test2: AnkiHubClient):
    client = authorized_client_for_user_test2

    client.suggest_optional_tags(
        suggestions=[
            OptionalTagSuggestion(
                ah_nid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
                tag_group_name="test100",
                deck_extension_id=999,
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
        ],
    )
    # we have no easy way to check if the suggestion is created if it is not accepted,
    # so this test just checks that the request is successful


@pytest.mark.vcr()
def test_suggest_auto_accepted_optional_tags(
    authorized_client_for_user_test1: AnkiHubClient,
):
    client = authorized_client_for_user_test1

    client.suggest_optional_tags(
        auto_accept=True,
        suggestions=[
            OptionalTagSuggestion(
                ah_nid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
                tag_group_name="test100",
                deck_extension_id=DECK_EXTENSION_ID,
                tags=[
                    "AnkiHub_Optional::test100::new1",
                    "AnkiHub_Optional::test100::new2",
                ],
            )
        ],
    )

    # assert that the tags were updated
    expected_response = DeckExtensionUpdateChunk(
        note_customizations=[
            NoteCustomization(
                ankihub_nid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
                tags=[
                    "AnkiHub_Optional::test100::new1",
                    "AnkiHub_Optional::test100::new2",
                ],
            ),
            NoteCustomization(
                ankihub_nid=uuid.UUID("b2344a94-0ca6-44a1-87a1-1593558c10a9"),
                tags=[
                    "AnkiHub_Optional::test100::test1",
                    "AnkiHub_Optional::test100::test2",
                ],
            ),
        ]
    )

    chunks = list(
        client.get_deck_extension_updates(
            deck_extension_id=DECK_EXTENSION_ID, since=None
        )
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    expected_response.latest_update = chunk.latest_update

    # sort tags to make sure they are in the same order
    for note_customization in chunk.note_customizations:
        note_customization.tags = sorted(note_customization.tags)

    for note_customization in expected_response.note_customizations:
        note_customization.tags = sorted(note_customization.tags)

    assert chunk == expected_response


def test_download_media(
    requests_mock: Mocker,
    next_deterministic_uuid: Callable[[], uuid.UUID],
):
    with tempfile.TemporaryDirectory() as temp_dir:

        client = AnkiHubClient(local_media_dir_path=Path(temp_dir))

        deck_id = next_deterministic_uuid()
        requests_mock.get(
            f"{DEFAULT_S3_BUCKET_URL}/deck_assets/{deck_id}/" + "image.png",
            content=b"test data",
        )
        client.download_media(media_names=["image.png"], deck_id=deck_id)

        assert (Path(temp_dir) / "image.png").exists()
        assert (Path(temp_dir) / "image.png").read_bytes() == b"test data"


class TestUploadMediaForSuggestion:
    @pytest.mark.parametrize(
        "suggestion_type", ["new_note_suggestion", "change_note_suggestion"]
    )
    def test_upload_media_for_suggestion(
        self,
        suggestion_type: str,
        requests_mock: Mocker,
        monkeypatch,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        remove_generated_media_files,
        request: FixtureRequest,
    ):
        client = AnkiHubClient(local_media_dir_path=TEST_MEDIA_PATH)

        suggestion: NoteSuggestion = request.getfixturevalue(suggestion_type)
        suggestion.fields[0].value = (
            '<img src="testfile_mario.png" width="100" alt="its-a me!">'
            '<div> something here <img src="testfile_test.jpeg" height="50" alt="just a test"> </div>'
        )

        fake_presigned_url = f"{client.s3_bucket_url}/fake_key"
        s3_upload_request_mock = requests_mock.post(
            fake_presigned_url, json={"success": True}, status_code=204
        )

        expected_media_name_map = {
            "testfile_mario.png": "156ca948cd1356b1a2c1c790f0855ad9.png",
            "testfile_test.jpeg": "a61eab59692d17a2adf4d1c5e9049ee4.jpeg",
        }

        suggestion_request_mock = None

        monkeypatch.setattr(
            AnkiHubClient,
            "_get_presigned_url_for_multiple_uploads",
            lambda *args, **kwargs: {
                "url": fake_presigned_url,
                "fields": {
                    "key": "deck_images/test/${filename}",
                    "x-amz-algorithm": "XXXXXX",
                    "x-amz-credential": "XXXXXX",
                    "x-amz-date": "20230321T162818Z",
                    "policy": "test_asuiHGIUWEHF78Y4QFBY24UIWBFV22FV428Y",
                    "x-amz-signature": "test_822ac386d1ece605db8cfca",
                },
            },
        )

        if isinstance(suggestion, ChangeNoteSuggestion):
            suggestion_request_mock = requests_mock.post(
                f"{DEFAULT_API_URL}/notes/{suggestion.ah_nid}/suggestion/",
                status_code=201,
            )

            client.create_change_note_suggestion(change_note_suggestion=suggestion)
        else:
            assert isinstance(suggestion, NewNoteSuggestion)
            suggestion_request_mock = requests_mock.post(
                f"{DEFAULT_API_URL}/decks/{suggestion.ah_did}/note-suggestion/",
                status_code=201,
            )
            client.create_new_note_suggestion(new_note_suggestion=suggestion)

        original_media_names = get_media_names_from_suggestion(suggestion)
        original_media_paths = [
            TEST_MEDIA_PATH / original_media_name
            for original_media_name in original_media_names
        ]
        media_name_map = client.generate_media_files_with_hashed_names(
            original_media_paths
        )
        new_media_paths = {
            TEST_MEDIA_PATH / media_name_map[original_media_path.name]
            for original_media_path in original_media_paths
        }

        client.upload_media(new_media_paths, ah_did=next_deterministic_uuid())

        # assert that the suggestion was made
        assert len(suggestion_request_mock.request_history) == 1  # type: ignore

        # assert that the zipfile with the media files was uploaded
        assert len(s3_upload_request_mock.request_history) == 1  # type: ignore

        # assert that the media name map was returned correctly
        assert media_name_map == expected_media_name_map

    def test_generate_media_files_with_hashed_names(self, remove_generated_media_files):
        client = AnkiHubClient(local_media_dir_path=TEST_MEDIA_PATH)

        filenames = [
            TEST_MEDIA_PATH / "testfile_mario.png",
            TEST_MEDIA_PATH / "testfile_anki.gif",
            TEST_MEDIA_PATH / "testfile_test.jpeg",
            TEST_MEDIA_PATH / "testfile_sound.mp3",
        ]

        expected_result = {
            "testfile_mario.png": "156ca948cd1356b1a2c1c790f0855ad9.png",
            "testfile_anki.gif": "87617b1d58967eb86b9e0e5dc92d91ee.gif",
            "testfile_test.jpeg": "a61eab59692d17a2adf4d1c5e9049ee4.jpeg",
            "testfile_sound.mp3": "ae9120835f658f1ae57e5754811a9475.mp3",
        }

        media_name_map = client.generate_media_files_with_hashed_names(filenames)
        assert media_name_map == expected_result


class TestUploadMediaForDeck:
    def notes_data_with_many_media_files(self) -> List[NoteInfo]:
        notes_data = [
            NoteInfoFactory.create(),
            NoteInfoFactory.create(),
            NoteInfoFactory.create(),
            NoteInfoFactory.create(),
        ]

        notes_data[0].fields[0].value = (
            '<img src="testfile_mario.png" width="100" alt="its-a me!">'
            '<div> something here <img src="testfile_test.jpeg" height="50" alt="just a test"> </div>'
        )
        notes_data[1].fields[
            0
        ].value = '<span> <p> <img src="testfile_anki.gif" width="100""> test text </p> <span>'

        notes_data[2].fields[1].value = (
            '<img src="testfile_1.jpeg" width="100" alt="test file 1">'
            '<div> something here <img src="testfile_2.jpeg" height="50" alt="test file 2"> </div>'
            '<img src="testfile_3.jpeg" width="100" alt="test file 3">'
            '<div> something here <img src="testfile_4.jpeg" height="50" alt="test file 4"> </div>'
            '<img src="testfile_5.jpeg" width="100" alt="test file 5">'
        )

        notes_data[3].fields[0].value = (
            '<img src="testfile_6.jpeg" width="100" alt="test file 6">'
            '<div> something here <img src="testfile_7.jpeg" height="50" alt="test file 7"> </div>'
        )

        notes_data[3].fields[1].value = (
            '<img src="testfile_8.jpeg" width="100" alt="test file 8">'
            '<div> something here <img src="testfile_9.jpeg" height="50" alt="test file 9"> </div>'
            '<img src="testfile_10.jpeg" width="100" alt="test file 10">'
            "[sound:testfile_sound.mp3]"
        )

        return notes_data

    def test_zips_media_files_from_deck_notes(
        self, next_deterministic_uuid: Callable[[], uuid.UUID], monkeypatch: MonkeyPatch
    ):
        client = AnkiHubClient(local_media_dir_path=TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()

        # Mock os.remove so the zip is not deleted
        os_remove_mock = MagicMock()
        monkeypatch.setattr(os, "remove", os_remove_mock)

        # Mock upload-related stuff
        monkeypatch.setattr(
            client, "_get_presigned_url_for_multiple_uploads", MagicMock()
        )
        monkeypatch.setattr(
            client, "_upload_file_to_s3_with_reusable_presigned_url", MagicMock()
        )

        deck_id = next_deterministic_uuid()
        self._upload_media_for_notes_data(client, notes_data, deck_id)

        # We will create and check for just one chunk in this test
        path_to_created_zip_file = Path(
            TEST_MEDIA_PATH / f"{deck_id}_0_deck_assets_part.zip"
        )

        all_media_names_in_notes = get_media_names_from_notes_data(notes_data)
        assert path_to_created_zip_file.is_file()
        assert len(all_media_names_in_notes) == 14
        with zipfile.ZipFile(path_to_created_zip_file, "r") as zip_ref:
            assert set(zip_ref.namelist()) == set(all_media_names_in_notes)

        # Remove the zipped file at the end of the test
        monkeypatch.undo()
        os.remove(path_to_created_zip_file)
        assert path_to_created_zip_file.is_file() is False

    def test_uploads_generated_zipped_file(
        self, next_deterministic_uuid: Callable[[], uuid.UUID], monkeypatch: MonkeyPatch
    ):
        client = AnkiHubClient(local_media_dir_path=TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()
        deck_id = next_deterministic_uuid()
        path_to_created_zip_file = Path(
            TEST_MEDIA_PATH / f"{deck_id}_0_deck_assets_part.zip"
        )

        s3_info_mocked_value = {
            "url": "https://fake_s3.com",
            "fields": {
                "key": "deck_images/test/${filename}",
                "x-amz-algorithm": "XXXXXX",
                "x-amz-credential": "XXXXXX",
                "x-amz-date": "20230321T162818Z",
                "policy": "test_asuiHGIUWEHF78Y4QFBY24UIWBFV22FV428Y",
                "x-amz-signature": "test_822ac386d1ece605db8cfca",
            },
        }
        get_presigned_url_mock = MagicMock()
        get_presigned_url_mock.return_value = s3_info_mocked_value
        monkeypatch.setattr(
            client, "_get_presigned_url_for_multiple_uploads", get_presigned_url_mock
        )

        mocked_upload_file_to_s3 = MagicMock()
        monkeypatch.setattr(
            client,
            "_upload_file_to_s3_with_reusable_presigned_url",
            mocked_upload_file_to_s3,
        )

        self._upload_media_for_notes_data(client, notes_data, deck_id)

        get_presigned_url_mock.assert_called_once_with(prefix=f"deck_assets/{deck_id}")
        mocked_upload_file_to_s3.assert_called_once_with(
            s3_presigned_info=s3_info_mocked_value,
            filepath=path_to_created_zip_file,
        )

    def test_removes_zipped_file_after_upload(
        self, next_deterministic_uuid: Callable[[], uuid.UUID], monkeypatch: MonkeyPatch
    ):
        client = AnkiHubClient(local_media_dir_path=TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()

        # Mock upload-related stuff
        monkeypatch.setattr(
            client, "_get_presigned_url_for_multiple_uploads", MagicMock()
        )
        monkeypatch.setattr(
            client, "_upload_file_to_s3_with_reusable_presigned_url", MagicMock()
        )

        deck_id = next_deterministic_uuid()
        self._upload_media_for_notes_data(client, notes_data, deck_id)

        path_to_created_zip_file = Path(TEST_MEDIA_PATH / f"{deck_id}.zip")

        assert not path_to_created_zip_file.is_file()

    def _upload_media_for_notes_data(
        self, client: AnkiHubClient, notes_data: List[NoteInfo], ah_did: uuid.UUID
    ):
        media_names = get_media_names_from_notes_data(notes_data)
        media_paths = {TEST_MEDIA_PATH / media_name for media_name in media_names}
        client.upload_media(media_paths, ah_did)


@pytest.mark.vcr()
class TestOwnedDeckIds:
    def test_owned_deck_ids_for_user_test1(
        self, authorized_client_for_user_test1: AnkiHubClient
    ):
        client = authorized_client_for_user_test1
        assert [ID_OF_DECK_OF_USER_TEST1] == client.owned_deck_ids()

    def test_owned_deck_ids_for_user_test2(
        self, authorized_client_for_user_test2: AnkiHubClient
    ):
        client = authorized_client_for_user_test2
        assert [ID_OF_DECK_OF_USER_TEST2] == client.owned_deck_ids()
