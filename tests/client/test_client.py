import base64
import dataclasses
import gzip
import json
import os
import subprocess
import tempfile
import time
import uuid
import zipfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, cast
from unittest.mock import Mock

import pytest
import requests
import requests_mock
from pytest import FixtureRequest
from pytest_mock import MockerFixture
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
    DeckExtensionUpdateChunk,
    DeckMedia,
    DeckMediaUpdateChunk,
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
from ankihub.ankihub_client.models import (
    ANKIHUB_DATETIME_FORMAT_STR,
    CardReviewData,
    DailyCardReviewSummary,
    DeckUpdates,
    NotesActionChoices,
    UserDeckExtensionRelation,
)
from ankihub.gui.utils import deck_download_progress_cb

WEBAPP_COMPOSE_FILE = Path(os.getenv("WEBAPP_COMPOSE_FILE")) if os.getenv("WEBAPP_COMPOSE_FILE") else None

TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"

DECK_CSV = TEST_DATA_PATH / "deck_with_one_basic_note.csv"
DECK_CSV_GZ = TEST_DATA_PATH / "deck_with_one_basic_note.csv.gz"
DECK_CSV_WITH_ONE_DELETED_BASIC_NOTE = TEST_DATA_PATH / "deck_with_one_deleted_basic_note.csv"

DECK_CSV_WITHOUT_DELETED_COLUMN = TEST_DATA_PATH / "deck_with_one_basic_note_without_deleted_column.csv"
DECK_CSV_GZ_WITHOUT_DELETED_COLUMN = TEST_DATA_PATH / "deck_with_one_basic_note_without_deleted_column.csv.gz"

TEST_MEDIA_PATH = TEST_DATA_PATH / "media"

VCR_CASSETTES_PATH = Path(__file__).parent / "cassettes"


# defined in create_fixture_data.py script in django app
DECK_WITH_EXTENSION_UUID = uuid.UUID("100df7b9-7749-4fe0-b801-e3dec1decd72")
DECK_EXTENSION_ID = 999

LOCAL_API_URL = "http://localhost:8000/api"


ID_OF_DECK_OF_USER_TEST1 = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")
ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1 = 1

ID_OF_DECK_OF_USER_TEST2 = uuid.UUID("5528aef7-f7ac-406b-9b35-4eaf00de4b20")

DATETIME_OF_ADDING_FIRST_DECK_MEDIA = datetime(year=2023, month=1, day=2, tzinfo=timezone.utc)

ID_OF_DECK_WITH_NOTES_ACTION = uuid.UUID("3d124f2e-a15c-4cf9-a470-b2ab8015debe")

DJANGO_CONTAINER_NAME = "django"

DB_NAME = "ankihub"
DB_USERNAME = "user"
DB_DUMP_FILE_NAME = f"{DB_NAME}.dump"
DB_CONTAINER_NAME = "postgres"

NO_SUCH_FILE_OR_DIRECTORY_MESSAGE = "No such file or directory"


@pytest.fixture
def client_with_server_setup(vcr: VCR, marks: List[str], request: FixtureRequest):
    """Resets the server database to an initial state before each test.
    If VCR is used (playback mode), this step is skipped.
    Yields a client that is logged in.
    """

    if "skipifvcr" in marks and vcr_enabled(vcr):
        pytest.skip("Skipping test because test has skipifvcr mark and VCR is enabled")

    if not is_playback_mode(vcr, request):
        create_db_dump_if_not_exists()

        # Restore DB from dump using a filtered TOC list that excludes the
        # SCHEMA entry. pg_restore --clean tries to DROP SCHEMA public which
        # fails when extensions (pg_trgm, vector) depend on it. By filtering
        # out the SCHEMA entry we still get full --clean behavior for tables
        # (drop + recreate) without touching the schema itself.
        toc_list_path = "/tmp/restore_toc.list"
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                DB_CONTAINER_NAME,
                "bash",
                "-c",
                (f"set -o pipefail; pg_restore -l {DB_DUMP_FILE_NAME} | grep -v ' SCHEMA ' > {toc_list_path}"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        report_command_result(
            command_name="pg_restore TOC list",
            result=result,
            raise_on_error=True,
        )

        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                DB_CONTAINER_NAME,
                "pg_restore",
                f"--dbname={DB_NAME}",
                f"--username={DB_USERNAME}",
                "--format=custom",
                "--clean",
                "--if-exists",
                "-L",
                toc_list_path,
                "--jobs=4",
                DB_DUMP_FILE_NAME,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        report_command_result(
            # We don't want to raise an error here because pg_restore might return some warnings which
            # can be ignored.
            command_name="pg_restore",
            result=result,
            raise_on_error=False,
        )

        _wait_for_server(
            api_url=LOCAL_API_URL,
            timeout=30.0,
            interval=0.5,
        )

    client = AnkiHubClient(api_url=LOCAL_API_URL, local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)
    yield client


def _wait_for_server(api_url: str, timeout: float = 30.0, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(api_url, timeout=1.0)
            if resp.status_code < 500:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(interval)
    pytest.fail(f"Could not connect to {api_url} after {timeout:.0f}s")


def is_playback_mode(vcr: VCR, request: FixtureRequest) -> bool:
    """Playback mode is when the test is run using the recorded HTTP responses (VCR)."""
    cassette_name = ".".join(request.node.nodeid.split("::")[1:]) + ".yaml"
    cassette_path = VCR_CASSETTES_PATH / cassette_name
    result = vcr_enabled(vcr) and cassette_path.exists()
    return result


def create_db_dump_if_not_exists() -> None:
    """Create a DB dump with the initial state of the DB if it doesn't exist yet.
    The DB is restored to this state before each test."""

    # Check if DB dump exists
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            DB_CONTAINER_NAME,
            "ls",
            DB_DUMP_FILE_NAME,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report_command_result(command_name="ls", result=result, raise_on_error=False)

    if result.stdout.strip() == DB_DUMP_FILE_NAME:
        # DB dump exists, no need to create it
        return
    elif NO_SUCH_FILE_OR_DIRECTORY_MESSAGE in result.stderr:
        # DB dump doesn't exist, create it
        pass
    else:
        assert False, f"Command ls failed with error code {result.returncode}"

    # Prepare the DB state
    result = subprocess.run(
        [
            "docker",
            "exec",
            DJANGO_CONTAINER_NAME,
            "bash",
            "-c",
            (
                "python manage.py flush --no-input && "
                "python manage.py migrate && "
                "python manage.py runscript create_fixture_data"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report_command_result(command_name="db setup", result=result, raise_on_error=True)

    # Dump the DB to a file to be able to restore it before each test
    result = subprocess.run(
        [
            "docker",
            "exec",
            DB_CONTAINER_NAME,
            "bash",
            "-c",
            (
                f"pg_dump --dbname={DB_NAME} --username={DB_USERNAME} "
                "--format=custom --schema=public "
                f"> {DB_DUMP_FILE_NAME}"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report_command_result(command_name="pg_dump", result=result, raise_on_error=True)


@pytest.fixture(scope="session", autouse=True)
def remove_db_dump() -> Generator:
    """Remove the db dump on the start of the session so that it is re-created for each session."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            DB_CONTAINER_NAME,
            "rm",
            DB_DUMP_FILE_NAME,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    report_command_result(command_name="rm", result=result, raise_on_error=False)
    if result.returncode == 0 or NO_SUCH_FILE_OR_DIRECTORY_MESSAGE in result.stderr:
        # Nothing to do
        pass
    elif result.returncode == 1 and "No such container" in result.stderr:
        # Nothing to do
        pass
    elif "container" in result.stderr.lower() and "is not running" in result.stderr:
        # Container is not running, nothing to do
        pass
    elif "docker: command not found" in result.stderr:
        pass
        # docker is not installed, nothing to do
    else:
        assert False, f"Command rm failed with error code {result.returncode} {result.stderr}"

    yield


def report_command_result(command_name: str, result: subprocess.CompletedProcess, raise_on_error: bool) -> None:
    if result.returncode != 0:
        print(f"Command {command_name} failed with error code {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        if raise_on_error:
            assert False, f"Command {command_name} failed with error code {result.returncode}"
    else:
        print(f"Command {command_name} executed successfully.")
        print(f"Stdout: {result.stdout}")


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
        vcr.record_mode == "new_episodes" and vcr.before_record_response and vcr.before_record_response() is None
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
            Field(name="Text", value="text1"),
            Field(name="Extra", value="extra1"),
        ],
        tags=["tag1", "tag2"],
        guid="asdf",
        comment="comment1",
        ah_did=ah_nid,
        note_type_name="Cloze (test1)",
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
            Field(name="Text", value="text1"),
            Field(name="Extra", value="extra1"),
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
            Field(name="Text", value="text2"),
            Field(name="Extra", value="extra2"),
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
class TestDownloadDeck:
    @pytest.mark.parametrize(
        "deck_file",
        [
            # The deck file can be either a CSV or a GZipped CSV
            DECK_CSV,
            DECK_CSV_GZ,
            # Previously CSVs didn't have the deleted column and some CSV might still not have it
            DECK_CSV_WITHOUT_DELETED_COLUMN,
            DECK_CSV_GZ_WITHOUT_DELETED_COLUMN,
            # This deck has one note that was deleted. In this case the last_update_type should be DELETE
            # (we don't have an extra field on the NoteInfo to indicate that the note was deleted).
            DECK_CSV_WITH_ONE_DELETED_BASIC_NOTE,
        ],
    )
    def test_download_deck(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        mocker: MockerFixture,
        deck_file: Path,
    ):
        client = authorized_client_for_user_test1
        presigned_url_suffix = f"/{deck_file.name}"
        mocker.patch.object(client, "_presigned_url_suffix_from_key", return_value=presigned_url_suffix)

        original_get_deck_by_id = client.get_deck_by_id

        def get_deck_by_id(*args, **kwargs) -> Deck:
            result = original_get_deck_by_id(*args, **kwargs)
            result.csv_notes_filename = deck_file.name
            return result

        mocker.patch.object(client, "get_deck_by_id", side_effect=get_deck_by_id)

        with requests_mock.Mocker(real_http=True) as m:
            m.get(
                f"{client.s3_bucket_url}{presigned_url_suffix}",
                content=deck_file.read_bytes(),
            )
            notes_data = client.download_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
        assert len(notes_data) == 1
        assert notes_data[0].tags == ["asdf"]

        if deck_file.name == DECK_CSV_WITH_ONE_DELETED_BASIC_NOTE.name:
            assert notes_data[0].last_update_type == SuggestionType.DELETE
        else:
            # Notes which are not deleted should have last_update_type set to None
            assert notes_data[0].last_update_type is None

    @pytest.mark.vcr()
    def test_download_deck_with_progress(self, authorized_client_for_user_test1: AnkiHubClient, mocker: MockerFixture):
        client = authorized_client_for_user_test1

        presigned_url_suffix = "/fake_key"
        mocker.patch.object(client, "_presigned_url_suffix_from_key", return_value=presigned_url_suffix)

        original_get_deck_by_id = client.get_deck_by_id

        def get_deck_by_id(*args, **kwargs) -> Deck:
            result = original_get_deck_by_id(*args, **kwargs)
            result.csv_notes_filename = "notes.csv"
            return result

        mocker.patch.object(client, "get_deck_by_id", side_effect=get_deck_by_id)

        with requests_mock.Mocker(real_http=True) as m:
            m.get(
                f"{client.s3_bucket_url}{presigned_url_suffix}",
                content=DECK_CSV.read_bytes(),
                headers={"content-length": "1000000"},
            )
            notes_data = client.download_deck(
                ah_did=ID_OF_DECK_OF_USER_TEST1,
                download_progress_cb=deck_download_progress_cb,
            )
        assert len(notes_data) == 1
        assert notes_data[0].tags == ["asdf"]

    def test_download_deck_with_presigned_url_argument(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        deck_file = DECK_CSV_GZ
        deck_file_presigned_url = f"{DEFAULT_S3_BUCKET_URL}/{deck_file.name}?auth=123"
        with requests_mock.Mocker(real_http=True) as m:
            m.get(
                deck_file_presigned_url,
                content=deck_file.read_bytes(),
            )
            notes_data = client.download_deck(
                ah_did=ID_OF_DECK_OF_USER_TEST1,
                s3_presigned_url=deck_file_presigned_url,
            )

        assert len(notes_data) == 1
        assert notes_data[0].tags == ["asdf"]


def create_note_on_ankihub_and_assert(client, new_note_suggestion, uuid_of_deck: uuid.UUID):
    # utility function meant to be used in tests for creating a note with known values on ankihub
    # asserts that the note was created correctly
    assert isinstance(client, AnkiHubClient)
    assert isinstance(new_note_suggestion, NewNoteSuggestion)

    # create an auto-accepted new note suggestion
    new_note_suggestion.ah_did = uuid_of_deck
    errors_by_nid = client.create_suggestions_in_bulk(new_note_suggestions=[new_note_suggestion], auto_accept=True)
    assert errors_by_nid == {}

    # assert that note was created
    note = client.get_note_by_id(ah_nid=new_note_suggestion.ah_nid)
    assert note.fields == new_note_suggestion.fields
    assert set(note.tags) == set(new_note_suggestion.tags)


@pytest.mark.vcr()
def test_upload_deck(
    authorized_client_for_user_test1: AnkiHubClient,
    next_deterministic_id: Callable[[], int],
    mocker: MockerFixture,
):
    client = authorized_client_for_user_test1

    note_data = NoteInfoFactory.create()

    # create the deck on AnkiHub
    # upload to s3 is mocked out, this will potentially cause errors on the locally running AnkiHub
    # because the deck will not be uploaded to s3, but we don't care about that here
    mocker.patch.object(client, "_presigned_url_suffix_from_key", return_value="fake_key")

    response_mock_1 = Mock()
    response_mock_1.status_code = 200

    response_mock_2 = Mock()
    response_mock_2.status_code = 201
    deck_id = uuid.uuid4()
    response_mock_2.json = lambda: {"deck_id": str(deck_id)}

    send_request_mock = mocker.patch.object(client, "_send_request", side_effect=[response_mock_1, response_mock_2])

    client.upload_deck(
        deck_name="test deck",
        notes_data=[note_data],
        note_types_data=[],
        anki_deck_id=next_deterministic_id(),
        private=False,
    )

    # Check that the deck would be uploaded to s3
    payload = json.loads(gzip.decompress(send_request_mock.call_args_list[0].kwargs["data"]).decode("utf-8"))
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
            Field(name="Text", value="text2"),
        ]

        # ... this shouldn't raise an exception
        client.create_change_note_suggestion(
            change_note_suggestion=cns,
            auto_accept=True,
        )

    @pytest.mark.vcr()
    @pytest.mark.parametrize(
        "auto_accept",
        [True, False],
    )
    def test_create_deletion_suggestion(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
        new_note_suggestion: NewNoteSuggestion,
        change_note_suggestion: ChangeNoteSuggestion,
        auto_accept: bool,
    ):
        client = authorized_client_for_user_test1

        # Setup a note on the server
        create_note_on_ankihub_and_assert(
            client,
            new_note_suggestion,
            ID_OF_DECK_OF_USER_TEST1,
        )

        # Create a deletion suggestion
        change_note_suggestion = ChangeNoteSuggestion(
            ah_nid=new_note_suggestion.ah_nid,
            anki_nid=new_note_suggestion.anki_nid,
            fields=[],
            added_tags=[],
            removed_tags=[],
            comment="test",
            change_type=SuggestionType.DELETE,
        )

        client.create_change_note_suggestion(
            change_note_suggestion=change_note_suggestion,
            auto_accept=auto_accept,
        )

        if auto_accept:
            # Assert that the note was deleted
            with pytest.raises(AnkiHubHTTPError):
                client.get_note_by_id(ah_nid=new_note_suggestion.ah_nid)
        else:
            # Assert that the note is still there
            note = client.get_note_by_id(ah_nid=new_note_suggestion.ah_nid)
            assert note.fields == new_note_suggestion.fields
            assert set(note.tags) == set(new_note_suggestion.tags)


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
        errors_by_nid = client.create_suggestions_in_bulk(new_note_suggestions=[new_note_suggestion], auto_accept=False)
        assert errors_by_nid == {}

        # try creating a new note suggestion with the same ah_nid as the first one
        new_note_suggestion_2 = deepcopy(new_note_suggestion)
        new_note_suggestion_2.anki_nid = 2
        errors_by_nid = client.create_suggestions_in_bulk(new_note_suggestions=[new_note_suggestion], auto_accept=False)
        assert len(errors_by_nid) == 1 and "Suggestion with this id already exists" in str(errors_by_nid)

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
        assert set(note.tags) == set(change_note_suggestion.added_tags) | set(new_note_suggestion.tags)

        # create a change note suggestion without any changes
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=False
        )
        assert len(
            errors_by_nid
        ) == 1 and "Suggestion fields and tags don't have any changes to the original note" in str(errors_by_nid)


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
        deck_of_user_test2 = next(deck for deck in decks if deck.ah_did == ID_OF_DECK_OF_USER_TEST2)
        assert deck_of_user_test2.user_relation == UserDeckRelation.SUBSCRIBER


class TestGetOwnedDecks:
    @pytest.mark.vcr()
    def test_basic(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1

        decks = client.get_owned_decks()
        assert len(decks) == 1
        assert decks[0].ah_did == ID_OF_DECK_OF_USER_TEST1


class TestGetDeckUpdates:
    @pytest.mark.vcr()
    def test_get_deck_updates(self, authorized_client_for_user_test2: AnkiHubClient):
        client = authorized_client_for_user_test2
        deck_updates = client.get_deck_updates(ID_OF_DECK_OF_USER_TEST2, since=None)
        assert len(deck_updates.notes) == 13

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
        deck_updates = client.get_deck_updates(ah_did=ID_OF_DECK_OF_USER_TEST1, since=since_time)

        note_info: NoteInfo = new_note_suggestion_note_info
        note_info.ah_nid = new_note_suggestion.ah_nid
        note_info.anki_nid = new_note_suggestion.anki_nid
        note_info.guid = new_note_suggestion.guid

        assert deck_updates == DeckUpdates(
            latest_update=deck_updates.latest_update,
            notes=[note_info],
            protected_fields={},
            protected_tags=[],
        )

    @pytest.mark.vcr()
    def test_get_deck_updates_with_external_notes_url(
        self, authorized_client_for_user_test1: AnkiHubClient, mocker: MockerFixture
    ):
        # This test mocks the responses instead of relying on real responses from the server,
        # because the setup required to get real responses with non-null external_notes_url is too costly or complex.
        client = authorized_client_for_user_test1

        # Mock responses from deck updates endpoint
        latest_update = datetime.now(timezone.utc)
        note1_from_csv = NoteInfoFactory.create()
        note1_from_json = NoteInfoFactory.create(ah_nid=note1_from_csv.ah_nid)
        note2_from_csv = NoteInfoFactory.create()

        response_with_csv_notes = self._deck_updates_response_mock_with_csv_notes(
            notes=[note1_from_csv, note2_from_csv],
            latest_update=latest_update,
            mocker=mocker,
        )

        response_with_json_notes = self._deck_updates_response_mock_with_json_notes(
            notes=[note1_from_json],
            latest_update=latest_update,
        )

        mocker.patch(
            "ankihub.ankihub_client.ankihub_client.AnkiHubClient._send_request",
            side_effect=[response_with_csv_notes, response_with_json_notes],
        )

        # Assert that the deck updates are as expected.
        # For note1, which is present in both the CSV and JSON responses, the note from the JSON should be used.
        # (The note from the JSON can be more recent than the one from the CSV.)
        deck_updates = client.get_deck_updates(ID_OF_DECK_OF_USER_TEST1, since=None)
        assert deck_updates.notes == [note1_from_json, note2_from_csv]
        assert deck_updates.latest_update == latest_update

    @pytest.mark.vcr()
    def test_get_empty_deck_updates(self, authorized_client_for_user_test1: AnkiHubClient, mocker: MockerFixture):
        client = authorized_client_for_user_test1

        # Mock responses from deck updates endpoint
        response = self._deck_updates_response_mock_with_json_notes(
            notes=[],
            latest_update=None,
        )

        mocker.patch(
            "ankihub.ankihub_client.ankihub_client.AnkiHubClient._send_request",
            return_value=response,
        )

        # Assert that the deck updates are as expected.
        deck_updates = client.get_deck_updates(ID_OF_DECK_OF_USER_TEST1, since=None)
        assert deck_updates.notes == []
        assert deck_updates.latest_update is None

    def _deck_updates_response_mock_with_json_notes(
        self, notes: List[NoteInfo], latest_update: Optional[datetime]
    ) -> Mock:
        result = Mock()
        note_dicts = [note.to_dict() for note in notes]
        notes_encoded = gzip.compress(json.dumps(note_dicts).encode("utf-8"))
        notes_encoded = base64.b85encode(notes_encoded)
        result.json = lambda: {
            "external_notes_url": None,
            "next": None,
            "notes": notes_encoded,
            "latest_update": (datetime.strftime(latest_update, ANKIHUB_DATETIME_FORMAT_STR) if latest_update else None),
            "protected_fields": {},
            "protected_tags": [],
        }
        result.status_code = 200
        return result

    def _deck_updates_response_mock_with_csv_notes(
        self,
        notes: List[NoteInfo],
        latest_update: Optional[datetime],
        mocker: MockerFixture,
    ) -> Mock:
        result = Mock()
        result.json = lambda: {
            "external_notes_url": "test_url",
            "next": None,
            "notes": None,
            "latest_update": (datetime.strftime(latest_update, ANKIHUB_DATETIME_FORMAT_STR) if latest_update else None),
            "protected_fields": {},
            "protected_tags": [],
        }
        result.status_code = 200

        # Mock the download of the deck from the external_notes_url
        mocker.patch(
            "ankihub.ankihub_client.ankihub_client.AnkiHubClient.download_deck",
            return_value=notes,
        )
        return result


class TestGetDeckMediaUpdates:
    def setup_method(self):
        # Mimics the state of the deck media on the server.
        # The deck media is ordered by the time of creation with the most recent one being the first.
        self.deck_media_on_server = [
            DeckMedia(
                name=f"example_{i}.png",
                file_content_hash=f"{i}0000000000000000000000000000000",
                modified=datetime.now(tz=timezone.utc),  # will be ignored
                referenced_on_accepted_note=False,
                exists_on_s3=False,
                download_enabled=True,
            )
            for i in reversed(range(3))
        ]

    def _assert_media_as_expected(self, actual: List[DeckMedia], expected: List[DeckMedia]):
        # Convert DeckMedia objects to dicts
        actual_dicts = [dataclasses.asdict(media) for media in actual]
        expected_dicts = [dataclasses.asdict(media) for media in expected]

        # Remove the 'modified' field from all dictionaries
        for a in actual_dicts:
            a.pop("modified", None)
        for e in expected_dicts:
            e.pop("modified", None)

        assert actual_dicts == expected_dicts

    @pytest.mark.vcr()
    def test_get_all_media(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        update_chunks: List[DeckMediaUpdateChunk] = list(
            client.get_deck_media_updates(ID_OF_DECK_OF_USER_TEST1, since=None)
        )

        assert len(update_chunks) == 1
        assert len(update_chunks[0].media) == 3
        deck_media_objects = update_chunks[0].media
        self._assert_media_as_expected(actual=deck_media_objects, expected=self.deck_media_on_server)

    @pytest.mark.vcr()
    def test_get_media_since(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        update_chunks: List[DeckMediaUpdateChunk] = list(
            client.get_deck_media_updates(
                ID_OF_DECK_OF_USER_TEST1,
                # by using this since value only DeckMedia added after the first DeckMedia will be returned
                since=DATETIME_OF_ADDING_FIRST_DECK_MEDIA,
            )
        )

        assert len(update_chunks) == 1
        assert len(update_chunks[0].media) == 2
        deck_media_objects = update_chunks[0].media
        self._assert_media_as_expected(
            # the oldest DeckMedia is not returned
            actual=deck_media_objects,
            expected=self.deck_media_on_server[:-1],
        )

    @pytest.mark.vcr()
    def test_pagination(self, authorized_client_for_user_test1: AnkiHubClient, mocker: MockerFixture):
        client = authorized_client_for_user_test1

        # Set page size to 1 so that we can test pagination
        page_size = 1
        mocker.patch(
            "ankihub.ankihub_client.ankihub_client.DECK_MEDIA_UPDATE_PAGE_SIZE",
            page_size,
        )

        update_chunks = list(client.get_deck_media_updates(ID_OF_DECK_OF_USER_TEST1, since=None))

        assert len(update_chunks) == 3

        deck_media_objects = [media for chunk in update_chunks for media in chunk.media]
        self._assert_media_as_expected(actual=deck_media_objects, expected=self.deck_media_on_server)


@pytest.mark.vcr()
def test_get_deck_extensions(
    authorized_client_for_user_test1: AnkiHubClient,
):
    client = authorized_client_for_user_test1

    response = client.get_deck_extensions()
    assert response == [
        DeckExtension(
            id=999,
            owner_id=1,
            ah_did=DECK_WITH_EXTENSION_UUID,
            name="test100",
            tag_group_name="test100",
            description="",
            user_relation=UserDeckExtensionRelation.OWNER,
        )
    ]


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
            user_relation=UserDeckExtensionRelation.OWNER,
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

    chunks = list(client.get_deck_extension_updates(deck_extension_id=deck_extension_id, since=None))
    assert len(chunks) == 1
    chunk = chunks[0]

    expected_response.latest_update = chunk.latest_update
    assert chunk == expected_response


@pytest.mark.vcr()
def test_get_media_disabled_fields(authorized_client_for_user_test1: AnkiHubClient):
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
    authorized_client_for_user_test1: AnkiHubClient, mocker: MockerFixture
):
    client = authorized_client_for_user_test1

    deck_extension_id = 999

    page_size = 1
    mocker.patch(
        "ankihub.ankihub_client.ankihub_client.DECK_EXTENSION_UPDATE_PAGE_SIZE",
        page_size,
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

    chunks = list(client.get_deck_extension_updates(deck_extension_id=deck_extension_id, since=None))
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
            errors=["This Deck Extension does not exist. Please create one for this Deck on AnkiHub."],
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

    chunks = list(client.get_deck_extension_updates(deck_extension_id=DECK_EXTENSION_ID, since=None))

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
        client = AnkiHubClient(local_media_dir_path_cb=lambda: Path(temp_dir))

        deck_id = next_deterministic_uuid()
        requests_mock.get(
            f"{DEFAULT_S3_BUCKET_URL}/deck_assets/{deck_id}/" + "image.png",
            content=b"test data",
        )
        client.download_media(media_names=["image.png"], deck_id=deck_id)

        assert (Path(temp_dir) / "image.png").exists()
        assert (Path(temp_dir) / "image.png").read_bytes() == b"test data"


class TestUploadMediaForSuggestion:
    @pytest.mark.parametrize("suggestion_type", ["new_note_suggestion", "change_note_suggestion"])
    def test_upload_media_for_suggestion(
        self,
        suggestion_type: str,
        requests_mock: Mocker,
        mocker: MockerFixture,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        remove_generated_media_files,
        request: FixtureRequest,
    ):
        client = AnkiHubClient(local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)

        suggestion: NoteSuggestion = request.getfixturevalue(suggestion_type)
        suggestion.fields[0].value = (
            '<img src="testfile_mario.png" width="100" alt="its-a me!">'
            '<div> something here <img src="testfile_test.jpeg" height="50" alt="just a test"> </div>'
        )

        fake_presigned_url = f"{client.s3_bucket_url}/fake_key"
        s3_upload_request_mock = requests_mock.post(fake_presigned_url, json={"success": True}, status_code=204)

        expected_media_name_map = {
            "testfile_mario.png": "156ca948cd1356b1a2c1c790f0855ad9.webp",
            "testfile_test.jpeg": "a61eab59692d17a2adf4d1c5e9049ee4.webp",
        }

        suggestion_request_mock = None

        mocker.patch.object(
            AnkiHubClient,
            "_get_presigned_url_for_multiple_uploads",
            return_value={
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

        original_media_names = get_media_names_from_suggestion(suggestion, Mock())
        original_media_paths = [TEST_MEDIA_PATH / original_media_name for original_media_name in original_media_names]
        media_name_map = client.generate_media_files_with_hashed_names(original_media_paths)
        new_media_paths = {
            TEST_MEDIA_PATH / media_name_map[original_media_path.name] for original_media_path in original_media_paths
        }

        client.upload_media(new_media_paths, ah_did=next_deterministic_uuid())

        # assert that the suggestion was made
        assert len(suggestion_request_mock.request_history) == 1  # type: ignore

        # assert that the zipfile with the media files was uploaded
        assert len(s3_upload_request_mock.request_history) == 1  # type: ignore

        # assert that the media name map was returned correctly
        assert media_name_map == expected_media_name_map

    def test_generate_media_files_with_hashed_names(self, remove_generated_media_files):
        client = AnkiHubClient(local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)

        filenames = [
            TEST_MEDIA_PATH / "testfile_mario.png",
            TEST_MEDIA_PATH / "testfile_anki.gif",
            TEST_MEDIA_PATH / "testfile_test.jpeg",
            TEST_MEDIA_PATH / "testfile_sound.mp3",
        ]

        expected_result = {
            "testfile_mario.png": "156ca948cd1356b1a2c1c790f0855ad9.webp",
            "testfile_anki.gif": "87617b1d58967eb86b9e0e5dc92d91ee.webp",
            "testfile_test.jpeg": "a61eab59692d17a2adf4d1c5e9049ee4.webp",
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
        notes_data[1].fields[0].value = '<span> <p> <img src="testfile_anki.gif" width="100""> test text </p> <span>'

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
        self,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        mocker: MockerFixture,
    ):
        client = AnkiHubClient(local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()

        # Mock upload-related stuff
        mocker.patch.object(client, "_get_presigned_url_for_multiple_uploads")
        mocker.patch.object(client, "_upload_file_to_s3_with_reusable_presigned_url")

        deck_id = next_deterministic_uuid()
        remove_mock = mocker.patch("os.remove")
        self._upload_media_for_notes_data(mocker, client, notes_data, deck_id)

        # We will create and check for just one chunk in this test
        path_to_created_zip_file = Path(TEST_MEDIA_PATH / f"{deck_id}_0_deck_assets_part.zip")

        all_media_names_in_notes = get_media_names_from_notes_data(notes_data, lambda mid: self._empty_notetype())
        assert path_to_created_zip_file.is_file()
        assert len(all_media_names_in_notes) == 14
        with zipfile.ZipFile(path_to_created_zip_file, "r") as zip_ref:
            assert set(zip_ref.namelist()) == set(all_media_names_in_notes)

        # Remove the zipped file at the end of the test
        mocker.stop(remove_mock)
        os.remove(path_to_created_zip_file)
        assert path_to_created_zip_file.is_file() is False

    def test_uploads_generated_zipped_file(
        self, next_deterministic_uuid: Callable[[], uuid.UUID], mocker: MockerFixture
    ):
        client = AnkiHubClient(local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()
        deck_id = next_deterministic_uuid()
        path_to_created_zip_file = Path(TEST_MEDIA_PATH / f"{deck_id}_0_deck_assets_part.zip")

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
        get_presigned_url_mock = mocker.patch.object(
            client,
            "_get_presigned_url_for_multiple_uploads",
            return_value=s3_info_mocked_value,
        )
        mocked_upload_file_to_s3 = mocker.patch.object(
            client,
            "_upload_file_to_s3_with_reusable_presigned_url",
        )

        self._upload_media_for_notes_data(mocker, client, notes_data, deck_id)

        get_presigned_url_mock.assert_called_once_with(prefix=f"deck_assets/{deck_id}")
        mocked_upload_file_to_s3.assert_called_once_with(
            s3_presigned_info=s3_info_mocked_value,
            filepath=path_to_created_zip_file,
        )

    def test_removes_zipped_file_after_upload(
        self, next_deterministic_uuid: Callable[[], uuid.UUID], mocker: MockerFixture
    ):
        client = AnkiHubClient(local_media_dir_path_cb=lambda: TEST_MEDIA_PATH)

        notes_data = self.notes_data_with_many_media_files()

        # Mock upload-related stuff
        mocker.patch.object(client, "_get_presigned_url_for_multiple_uploads")
        mocker.patch.object(client, "_upload_file_to_s3_with_reusable_presigned_url")

        deck_id = next_deterministic_uuid()
        self._upload_media_for_notes_data(mocker, client, notes_data, deck_id)

        path_to_created_zip_file = Path(TEST_MEDIA_PATH / f"{deck_id}.zip")

        assert not path_to_created_zip_file.is_file()

    @staticmethod
    def _empty_notetype() -> Dict[str, Any]:
        return {"css": "", "tmpls": []}

    def _upload_media_for_notes_data(
        self, mocker: MockerFixture, client: AnkiHubClient, notes_data: List[NoteInfo], ah_did: uuid.UUID
    ):
        media_names = get_media_names_from_notes_data(notes_data, lambda mid: self._empty_notetype())
        media_paths = {TEST_MEDIA_PATH / media_name for media_name in media_names}
        client.upload_media(media_paths, ah_did)


@pytest.mark.vcr()
class TestOwnedDeckIds:
    def test_owned_deck_ids_for_user_test1(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        assert [ID_OF_DECK_OF_USER_TEST1] == client.owned_deck_ids()

    def test_owned_deck_ids_for_user_test2(self, authorized_client_for_user_test2: AnkiHubClient):
        client = authorized_client_for_user_test2
        assert [ID_OF_DECK_OF_USER_TEST2] == client.owned_deck_ids()


@pytest.mark.vcr()
class TestGetNoteType:
    def test_get_note_type(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        note_type = client.get_note_type(anki_note_type_id=ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1)
        assert note_type["id"] == ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1
        assert note_type["name"] == "Cloze (test1)"

    def test_get_not_existing_note_type(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        with pytest.raises(AnkiHubHTTPError) as excinfo:
            client.get_note_type(anki_note_type_id=-1)

        assert cast(AnkiHubHTTPError, excinfo.value).response.status_code == 404


@pytest.mark.vcr()
class TestGetNoteTypesDictForDeck:
    def test_get_note_types_dict(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        note_types_by_id = client.get_note_types_dict_for_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
        assert len(note_types_by_id) == 1

        note_type = note_types_by_id[ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1]
        assert note_type["id"] == ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1
        assert note_type["name"] == "Cloze (test1)"


@pytest.mark.vcr()
class TestCreateNoteType:
    def test_create_note_type(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        note_type = {
            "id": 3,
            "name": "New Type",
            "flds": [{"name": "Front"}, {"name": "Back"}],
            "tmpls": [{"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{Back}}"}],
        }
        new_note_type = client.create_note_type(ID_OF_DECK_OF_USER_TEST1, note_type)
        deck = client.get_deck_by_id(ID_OF_DECK_OF_USER_TEST1)
        note_types_by_id = client.get_note_types_dict_for_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
        assert new_note_type["name"] == f"New Type ({deck.name} / test1)"
        assert len(note_types_by_id) == 2
        new_note_type = note_types_by_id[cast(int, note_type["id"])]
        assert new_note_type["id"] == note_type["id"]
        assert new_note_type["name"] == f"New Type ({deck.name} / test1)"


@pytest.mark.vcr()
class TestAddNoteTypeFields:
    def test_add_note_type_fields(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        note_types_by_id = client.get_note_types_dict_for_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
        note_type = note_types_by_id[ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1]
        field_names = ["New1", "New2"]
        for name in field_names:
            field = note_type["flds"][0].copy()
            field["name"] = name
            field["ord"] = None
            note_type["flds"].append(field)
        note_type = client.update_note_type(ID_OF_DECK_OF_USER_TEST1, note_type, ["flds"])
        assert all(field_name in [field["name"] for field in note_type["flds"]] for field_name in field_names)


@pytest.mark.vcr()
class TestUpdateNoteTypeTemplatesAndStyles:
    def test_update_note_type_templates_and_styles(self, authorized_client_for_user_test1: AnkiHubClient):
        css = ".home {background: red}"
        templates = [
            {
                "ord": 0,
                "afmt": "<div>back</div>",
                "name": "Test",
                "qfmt": "<div>front</div>",
                "bafmt": "{{cloze:Text}}",
                "bqfmt": "{{cloze:Text}}",
                "bsize": 12,
            }
        ]

        client = authorized_client_for_user_test1
        note_types_by_id = client.get_note_types_dict_for_deck(ah_did=ID_OF_DECK_OF_USER_TEST1)
        note_type = note_types_by_id[ANKI_ID_OF_NOTE_TYPE_OF_USER_TEST1]
        note_type["tmpls"] = templates
        note_type["css"] = css

        data = client.update_note_type(ID_OF_DECK_OF_USER_TEST1, note_type, ["tmpls", "css"])

        assert data["css"] == css
        assert data["tmpls"] == templates


@pytest.mark.vcr()
class TestGetFeatureFlags:
    def test_get_feature_flags(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        client.get_feature_flags()
        # This test just makes sure that the method does not throw an exception
        # Feature flags can change so we don't want to assert anything


@pytest.mark.vcr()
class TestGetUserDetails:
    def test_get_user_details(self, authorized_client_for_user_test1: AnkiHubClient):
        client = authorized_client_for_user_test1
        client.get_user_details()
        # This test just makes sure that the method does not throw an exception


@pytest.mark.vcr()
class TestSendCardReviewData:
    def test_basic(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        card_review_data = CardReviewData(
            ah_did=ID_OF_DECK_OF_USER_TEST1,
            total_card_reviews_last_7_days=10,
            total_card_reviews_last_30_days=20,
            first_card_review_at=now - timedelta(days=30),
            last_card_review_at=now,
        )

        authorized_client_for_user_test1.send_card_review_data([card_review_data])


@pytest.mark.vcr()
class TestSendDailyCardReviewSummaries:
    def test_basic(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ) -> None:
        daily_card_review_summaries = [
            DailyCardReviewSummary(
                review_session_date=date.today() - timedelta(days=1),
                total_cards_studied=1,
                total_cards_marked_as_again=1,
                total_time_reviewing=5,
            ),
            DailyCardReviewSummary(
                review_session_date=date.today(),
                total_cards_studied=2,
                total_cards_marked_as_again=1,
                total_cards_marked_as_good=1,
                total_time_reviewing=5,
            ),
        ]

        authorized_client_for_user_test1.send_daily_card_review_summaries(daily_card_review_summaries)


@pytest.mark.vcr
class TestGetPendingNotesActionsForDeck:
    def test_get_one_note_action(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        client.subscribe_to_deck(ID_OF_DECK_WITH_NOTES_ACTION)
        notes_actions = client.get_pending_notes_actions_for_deck(ID_OF_DECK_WITH_NOTES_ACTION)
        assert len(notes_actions) == 1
        assert notes_actions[0].action == NotesActionChoices.UNSUSPEND
        assert len(notes_actions[0].note_ids) == 1

    def test_notes_action_is_only_returned_once(
        self,
        authorized_client_for_user_test1: AnkiHubClient,
    ):
        client = authorized_client_for_user_test1
        client.subscribe_to_deck(ID_OF_DECK_WITH_NOTES_ACTION)
        notes_actions = client.get_pending_notes_actions_for_deck(ID_OF_DECK_WITH_NOTES_ACTION)
        assert len(notes_actions) == 1

        notes_actions = client.get_pending_notes_actions_for_deck(ID_OF_DECK_WITH_NOTES_ACTION)
        assert len(notes_actions) == 0

    def test_when_not_authorized(
        self,
        unauthorized_client: AnkiHubClient,
    ):
        client = unauthorized_client
        with pytest.raises(AnkiHubHTTPError):
            client.get_pending_notes_actions_for_deck(ID_OF_DECK_WITH_NOTES_ACTION)
