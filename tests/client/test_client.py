import os
import subprocess
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest
import requests_mock
from vcr import VCR

COMPOSE_FILE = Path(os.getenv("COMPOSE_FILE")) if os.getenv("COMPOSE_FILE") else None

TEST_DATA_PATH = Path(__file__).parent.parent / "test_data"
DECK_CSV = TEST_DATA_PATH / "deck_with_one_basic_note.csv"
DECK_CSV_GZ = TEST_DATA_PATH / "deck_with_one_basic_note.csv.gz"

VCR_CASSETTES_PATH = Path(__file__).parent / "cassettes"

UUID_1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
UUID_2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

# defined in create_fixture_data.py script in django app
DECK_WITH_EXTENSION_UUID = uuid.UUID("100df7b9-7749-4fe0-b801-e3dec1decd72")
DECK_EXTENSION_ID = 999


@pytest.fixture(autouse=True)
def set_ankihub_app_url():
    from ankihub import ankihub_client

    ankihub_client.API_URL_BASE = "http://localhost:8000/api"


@pytest.fixture
def client(vcr: VCR, request, marks):
    from ankihub.ankihub_client import AnkiHubClient

    if "skipifvcr" in marks and vcr_enabled(vcr):
        pytest.skip("Skipping test because test has skipifvcr mark and VCR is enabled")

    cassette_name = ".".join(request.node.nodeid.split("::")[1:]) + ".yaml"
    cassette_path = VCR_CASSETTES_PATH / cassette_name
    playback_mode = vcr_enabled(vcr) and cassette_path.exists()

    if not playback_mode:
        run_command_in_django_container("python manage.py runscript create_test_users")
        run_command_in_django_container(
            "python manage.py runscript create_fixture_data"
        )

    client = AnkiHubClient()
    yield client

    if not playback_mode:
        run_command_in_django_container("python manage.py flush --no-input")


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


def run_command_in_django_container(command):
    subprocess.run(
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
        ]
    )


@pytest.fixture
def authorized_client_for_user_test1(client):
    from ankihub.ankihub_client import AnkiHubClient

    client: AnkiHubClient = client
    credentials_data = {"username": "test1", "password": "asdf"}
    client.login(credentials=credentials_data)
    yield client


@pytest.fixture
def authorized_client_for_user_test2(client, request):
    from ankihub.ankihub_client import AnkiHubClient

    client: AnkiHubClient = client
    credentials_data = {"username": "test2", "password": "asdf"}
    client.login(credentials=credentials_data)
    yield client


@pytest.fixture
def uuid_of_deck_of_user_test1():
    return uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")


@pytest.fixture
def uuid_of_deck_of_user_test2():
    return uuid.UUID("5528aef7-f7ac-406b-9b35-4eaf00de4b20")


@pytest.fixture
def new_note_suggestion():
    from ankihub.ankihub_client import Field, NewNoteSuggestion

    return NewNoteSuggestion(
        ankihub_note_uuid=UUID_1,
        anki_nid=1,
        fields=[
            Field(name="Front", value="front1", order=0),
            Field(name="Back", value="back1", order=1),
        ],
        tags=["tag1", "tag2"],
        guid="asdf",
        comment="comment1",
        ankihub_deck_uuid=UUID_1,
        note_type_name="Basic",
        anki_note_type_id=1,
    )


@pytest.fixture
def new_note_suggestion_note_info():
    from ankihub.ankihub_client import Field, NoteInfo

    return NoteInfo(
        ankihub_note_uuid=UUID_1,
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
def change_note_suggestion():
    from ankihub.ankihub_client import ChangeNoteSuggestion, Field, SuggestionType

    return ChangeNoteSuggestion(
        ankihub_note_uuid=UUID_1,
        anki_nid=1,
        fields=[
            Field(name="Front", value="front2", order=0),
            Field(name="Back", value="back2", order=1),
        ],
        tags=["tag3", "tag4"],
        comment="comment1",
        change_type=SuggestionType.UPDATED_CONTENT,
    )


@pytest.mark.vcr()
def test_client_login_and_signout_with_username(client):
    credentials_data = {"username": "test1", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert len(token) == 64
    assert client.session.headers["Authorization"] == f"Token {token}"

    client.signout()
    assert client.session.headers["Authorization"] == ""


@pytest.mark.vcr()
def test_client_login_and_signout_with_email(client):
    credentials_data = {"email": "test1@email.com", "password": "asdf"}
    token = client.login(credentials=credentials_data)
    assert len(token) == 64
    assert client.session.headers["Authorization"] == f"Token {token}"

    client.signout()
    assert client.session.headers["Authorization"] == ""


@pytest.mark.vcr()
def test_download_deck(authorized_client_for_user_test1, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient, Deck

    client: AnkiHubClient = authorized_client_for_user_test1
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")

    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(client, "get_presigned_url", get_presigned_url)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri(
            "GET", get_presigned_url.return_value, content=DECK_CSV.read_bytes()
        )
        notes_data = client.download_deck(ankihub_deck_uuid=deck_id)
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_download_compressed_deck(authorized_client_for_user_test1, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient, Deck

    client: AnkiHubClient = authorized_client_for_user_test1
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")

    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(client, "get_presigned_url", get_presigned_url)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv.gz"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri(
            "GET", get_presigned_url.return_value, content=DECK_CSV_GZ.read_bytes()
        )
        notes_data = client.download_deck(ankihub_deck_uuid=deck_id)
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


@pytest.mark.vcr()
def test_download_deck_with_progress(authorized_client_for_user_test1, monkeypatch):
    from ankihub.ankihub_client import AnkiHubClient, Deck
    from ankihub.gui.decks import download_progress_cb

    client: AnkiHubClient = authorized_client_for_user_test1
    deck_id = uuid.UUID("dda0d3ad-89cd-45fb-8ddc-fabad93c2d7b")

    get_presigned_url = MagicMock()
    get_presigned_url.return_value = "https://fake_s3.com"
    monkeypatch.setattr(client, "get_presigned_url", get_presigned_url)

    original_get_deck_by_id = client.get_deck_by_id

    def get_deck_by_id(*args, **kwargs) -> Deck:
        result = original_get_deck_by_id(*args, **kwargs)
        result.csv_notes_filename = "notes.csv"
        return result

    monkeypatch.setattr(client, "get_deck_by_id", get_deck_by_id)

    with requests_mock.Mocker(real_http=True) as m:
        m.register_uri(
            "GET",
            get_presigned_url.return_value,
            content=DECK_CSV.read_bytes(),
            headers={"content-length": "1000000"},
        )
        notes_data = client.download_deck(
            ankihub_deck_uuid=deck_id, download_progress_cb=download_progress_cb
        )
    assert len(notes_data) == 1
    assert notes_data[0].tags == ["asdf"]


def create_note_on_ankihub_and_assert(client, new_note_suggestion, uuid_of_deck: uuid):
    # utility function meant to be used in tests for creating a note with known values on ankihub
    # asserts that the note was created correctly

    from ankihub.ankihub_client import AnkiHubClient, NewNoteSuggestion

    assert isinstance(client, AnkiHubClient)
    assert isinstance(new_note_suggestion, NewNoteSuggestion)

    # create an auto-accepted new note suggestion
    new_note_suggestion.ankihub_deck_uuid = uuid_of_deck
    errors_by_nid = client.create_suggestions_in_bulk(
        new_note_suggestions=[new_note_suggestion], auto_accept=True
    )
    assert errors_by_nid == {}

    # assert that note was created
    note = client.get_note_by_id(
        ankihub_note_uuid=new_note_suggestion.ankihub_note_uuid
    )
    assert note.fields == new_note_suggestion.fields
    assert set(note.tags) == set(new_note_suggestion.tags)


class TestCreateSuggestion:
    @pytest.mark.vcr()
    def test_create_change_note_suggestion_without_all_fields(
        self,
        authorized_client_for_user_test1,
        uuid_of_deck_of_user_test1,
        new_note_suggestion,
        change_note_suggestion,
    ):
        from ankihub.ankihub_client import AnkiHubClient, ChangeNoteSuggestion, Field

        client: AnkiHubClient = authorized_client_for_user_test1

        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            uuid_of_deck_of_user_test1,
        )

        # create a change note suggestion without all fields (for the same note)
        cns: ChangeNoteSuggestion = change_note_suggestion
        cns.ankihub_note_uuid = new_note_suggestion.ankihub_note_uuid
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
        authorized_client_for_user_test1,
        new_note_suggestion,
        uuid_of_deck_of_user_test1,
    ):
        from ankihub.ankihub_client import AnkiHubClient

        client: AnkiHubClient = authorized_client_for_user_test1

        new_note_suggestion.ankihub_deck_uuid = uuid_of_deck_of_user_test1
        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion],
            auto_accept=False,
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_create_two_new_note_suggestions(
        self,
        authorized_client_for_user_test1,
        new_note_suggestion,
        uuid_of_deck_of_user_test1,
    ):
        from ankihub.ankihub_client import AnkiHubClient

        client: AnkiHubClient = authorized_client_for_user_test1

        # create two new note suggestions at once
        new_note_suggestion.ankihub_deck_uuid = uuid_of_deck_of_user_test1

        new_note_suggestion_2 = deepcopy(new_note_suggestion)
        new_note_suggestion_2.ankihub_note_uuid = UUID_2
        new_note_suggestion_2.anki_nid = 2

        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion, new_note_suggestion_2],
            auto_accept=False,
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_use_same_ankihub_id_for_new_note_suggestion(
        self,
        authorized_client_for_user_test1,
        new_note_suggestion,
        uuid_of_deck_of_user_test1,
    ):
        from ankihub.ankihub_client import AnkiHubClient

        client: AnkiHubClient = authorized_client_for_user_test1

        # create a new note suggestion
        new_note_suggestion.ankihub_deck_uuid = uuid_of_deck_of_user_test1
        errors_by_nid = client.create_suggestions_in_bulk(
            new_note_suggestions=[new_note_suggestion], auto_accept=False
        )
        assert errors_by_nid == {}

        # try creating a new note suggestion with the same ankihub_note_uuid as the first one
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
        uuid_of_deck_of_user_test1,
    ):
        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            uuid_of_deck_of_user_test1,
        )

    @pytest.mark.vcr()
    def test_create_change_note_suggestion(
        self,
        authorized_client_for_user_test1,
        new_note_suggestion,
        change_note_suggestion,
        uuid_of_deck_of_user_test1,
    ):
        from ankihub.ankihub_client import AnkiHubClient

        client: AnkiHubClient = authorized_client_for_user_test1

        new_note_suggestion.ankihub_deck_uuid = uuid_of_deck_of_user_test1
        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            uuid_of_deck_of_user_test1,
        )

        # create a change note suggestion
        change_note_suggestion.ankihub_note_uuid = new_note_suggestion.ankihub_note_uuid
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=False
        )
        assert errors_by_nid == {}

    @pytest.mark.vcr()
    def test_create_auto_accepted_change_note_suggestion(
        self,
        authorized_client_for_user_test1,
        new_note_suggestion,
        change_note_suggestion,
        uuid_of_deck_of_user_test1,
    ):
        from ankihub.ankihub_client import AnkiHubClient

        client: AnkiHubClient = authorized_client_for_user_test1

        create_note_on_ankihub_and_assert(
            authorized_client_for_user_test1,
            new_note_suggestion,
            uuid_of_deck_of_user_test1,
        )

        # create an auto-accepted change note suggestion and assert that note was changed
        change_note_suggestion.ankihub_note_uuid = new_note_suggestion.ankihub_note_uuid
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=True
        )
        note = client.get_note_by_id(
            ankihub_note_uuid=new_note_suggestion.ankihub_note_uuid
        )
        assert errors_by_nid == {}
        assert note.fields == change_note_suggestion.fields
        assert set(note.tags) == set(change_note_suggestion.tags)

        # create a change note suggestion without any changes
        errors_by_nid = client.create_suggestions_in_bulk(
            change_note_suggestions=[change_note_suggestion], auto_accept=False
        )
        assert len(
            errors_by_nid
        ) == 1 and "Suggestion fields and tags don't have any changes to the original note" in str(
            errors_by_nid
        )


class TestGetDeckUpdates:
    @pytest.mark.vcr()
    def test_get_deck_updates(
        self, authorized_client_for_user_test2, uuid_of_deck_of_user_test2, monkeypatch
    ):
        from ankihub.ankihub_client import AnkiHubClient, DeckUpdateChunk

        client: AnkiHubClient = authorized_client_for_user_test2

        page_size = 5
        monkeypatch.setattr("ankihub.ankihub_client.DECK_UPDATE_PAGE_SIZE", page_size)
        update_chunks: List[DeckUpdateChunk] = list(
            client.get_deck_updates(uuid_of_deck_of_user_test2, since=None)
        )
        assert len(update_chunks) == 2
        assert all(len(chunk.notes) == page_size for chunk in update_chunks)

    @pytest.mark.skipifvcr()
    def test_get_deck_updates_since(
        self,
        authorized_client_for_user_test1,
        uuid_of_deck_of_user_test1,
        new_note_suggestion,
        new_note_suggestion_note_info,
        vcr,
    ):
        from ankihub.ankihub_client import AnkiHubClient, DeckUpdateChunk, NoteInfo

        client: AnkiHubClient = authorized_client_for_user_test1

        since_time = datetime.now(timezone.utc)

        # create a new note
        new_note_suggestion.ankihub_deck_uuid = uuid_of_deck_of_user_test1
        client.create_new_note_suggestion(new_note_suggestion, auto_accept=True)

        # get deck updates since the time of the new note creation
        chunks = list(
            client.get_deck_updates(
                ankihub_deck_uuid=uuid_of_deck_of_user_test1, since=since_time
            )
        )

        note_info: NoteInfo = new_note_suggestion_note_info
        note_info.ankihub_note_uuid = new_note_suggestion.ankihub_note_uuid
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
def test_get_deck_extensions_by_deck_id(authorized_client_for_user_test1):
    from ankihub.ankihub_client import AnkiHubClient, DeckExtension

    client: AnkiHubClient = authorized_client_for_user_test1

    deck_id = uuid.UUID("100df7b9-7749-4fe0-b801-e3dec1decd72")

    response = client.get_deck_extensions_by_deck_id(deck_id=deck_id)
    assert response == [
        DeckExtension(
            id=999,
            owner_id=1,
            ankihub_deck_uuid=uuid.UUID("100df7b9-7749-4fe0-b801-e3dec1decd72"),
            name="test100",
            tag_group_name="test100",
            description="",
        )
    ]


@pytest.mark.vcr()
def test_get_note_customizations_by_deck_extension_id(authorized_client_for_user_test1):
    from ankihub.ankihub_client import (
        AnkiHubClient,
        DeckExtensionUpdateChunk,
        NoteCustomization,
    )

    client: AnkiHubClient = authorized_client_for_user_test1

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
def test_get_note_customizations_by_deck_extension_id_in_multiple_chunks(
    authorized_client_for_user_test1, monkeypatch
):
    from ankihub.ankihub_client import (
        AnkiHubClient,
        DeckExtensionUpdateChunk,
        NoteCustomization,
    )

    client: AnkiHubClient = authorized_client_for_user_test1

    deck_extension_id = 999

    monkeypatch.setattr("ankihub.ankihub_client.DECK_EXTENSION_UPDATE_PAGE_SIZE", 1)

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
def test_prevalidate_tag_groups(authorized_client_for_user_test2):
    from ankihub.ankihub_client import AnkiHubClient, TagGroupValidationResponse

    client: AnkiHubClient = authorized_client_for_user_test2

    tag_group_validation_responses = client.prevalidate_tag_groups(
        ankihub_deck_uuid=DECK_WITH_EXTENSION_UUID,
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
def test_suggest_optional_tags(authorized_client_for_user_test2):
    from ankihub.ankihub_client import AnkiHubClient, OptionalTagSuggestion

    client: AnkiHubClient = authorized_client_for_user_test2

    client.suggest_optional_tags(
        suggestions=[
            OptionalTagSuggestion(
                ankihub_note_uuid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
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
def test_suggest_auto_accepted_optional_tags(authorized_client_for_user_test1):
    from ankihub.ankihub_client import (
        AnkiHubClient,
        DeckExtensionUpdateChunk,
        NoteCustomization,
        OptionalTagSuggestion,
    )

    client: AnkiHubClient = authorized_client_for_user_test1

    client.suggest_optional_tags(
        auto_accept=True,
        suggestions=[
            OptionalTagSuggestion(
                ankihub_note_uuid=uuid.UUID("8645c6d6-4f3d-417e-8295-8f5009042b6e"),
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
