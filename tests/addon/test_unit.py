import json
import logging
import os
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from logging import LogRecord
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable, Dict, Generator, List, Optional, Protocol, Tuple
from unittest.mock import MagicMock, Mock, call, patch

import aqt
import pytest
import requests
from anki.decks import DeckId
from anki.models import NotetypeDict
from anki.notes import Note, NoteId
from approvaltests.approvals import verify  # type: ignore
from approvaltests.namer import NamerFactory  # type: ignore
from aqt.qt import QDialog, QDialogButtonBox, QLineEdit, QMenu, Qt, QTimer, QWidget
from pytest import MonkeyPatch
from pytest_anki import AnkiSession
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot  # type: ignore
from requests import Response
from requests_mock import Mocker

from ankihub.gui.subdeck_due_date_dialog import (
    maybe_show_subdeck_due_date_reminders,
    show_subdeck_due_date_reminder,
)
from ankihub.main.block_exam_subdecks import (
    move_subdeck_to_main_deck,
    set_subdeck_due_date,
)
from ankihub.settings import BlockExamSubdeckConfig, BlockExamSubdeckOrigin

from ..factories import (
    AnkiHubImportResultFactory,
    DeckExtensionFactory,
    DeckFactory,
    DeckMediaFactory,
    NoteInfoFactory,
)
from ..fixtures import (  # type: ignore
    AddAnkiNote,
    ImportAHNote,
    ImportAHNoteType,
    InstallAHDeck,
    LatestInstanceTracker,
    MockStudyDeckDialogWithCB,
    MockSuggestionDialog,
    SetFeatureFlagState,
    add_basic_anki_note_to_deck,
    assert_datetime_equal_ignore_milliseconds,
    create_anki_deck,
    note_type_with_field_names,
    record_review_for_anki_nid,
)

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

import ankihub
from ankihub import entry_point
from ankihub.addon_ankihub_client import AddonAnkiHubClient
from ankihub.ankihub_client import (
    AnkiHubClient,
    AnkiHubHTTPError,
    Field,
    SuggestionType,
    TagGroupValidationResponse,
)
from ankihub.ankihub_client.ankihub_client import (
    DEFAULT_API_URL,
    DEFAULT_APP_URL,
    AnkiHubRequestException,
)
from ankihub.ankihub_client.models import (  # type: ignore
    CardReviewData,
    DailyCardReviewSummary,
    UserDeckExtensionRelation,
    UserDeckRelation,
)
from ankihub.db.db import _AnkiHubDB
from ankihub.db.exceptions import IntegrityError, MissingValueError
from ankihub.db.models import AnkiHubNote, DeckMedia, get_peewee_database
from ankihub.feature_flags import (
    _feature_flags_update_callbacks,
    add_feature_flags_update_callback,
    update_feature_flags_in_background,
)
from ankihub.gui import menu
from ankihub.gui.config_dialog import setup_config_dialog_manager
from ankihub.gui.error_dialog import ErrorDialog
from ankihub.gui.errors import (
    OUTDATED_CLIENT_RESPONSE_DETAIL,
    TERMS_AGREEMENT_NOT_ACCEPTED_DETAIL,
    _contains_path_to_this_addon,
    _normalize_url,
    _try_handle_exception,
    upload_logs_in_background,
)
from ankihub.gui.exceptions import DeckDownloadAndInstallError
from ankihub.gui.media_sync import media_sync
from ankihub.gui.menu import AnkiHubLogin, menu_state, refresh_ankihub_menu
from ankihub.gui.operations.deck_creation import (
    DeckCreationConfirmationDialog,
    create_collaborative_deck,
)
from ankihub.gui.operations.deck_installation import (
    _show_deck_import_summary_dialog_inner,
)
from ankihub.gui.operations.utils import future_with_exception, future_with_result
from ankihub.gui.optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
from ankihub.gui.suggestion_dialog import (
    SourceType,
    SuggestionDialog,
    SuggestionMetadata,
    SuggestionSource,
    _on_suggest_notes_in_bulk_done,
    get_anki_nid_to_ah_dids_dict,
    open_suggestion_dialog_for_bulk_suggestion,
    open_suggestion_dialog_for_single_suggestion,
)
from ankihub.gui.threading_utils import rate_limited
from ankihub.gui.utils import (
    _Dialog,
    ask_user,
    choose_ankihub_deck,
    extract_argument,
    show_dialog,
    show_error_dialog,
)
from ankihub.main import suggestions
from ankihub.main.deck_creation import DeckCreationResult
from ankihub.main.exporting import _prepared_field_html
from ankihub.main.importing import _updated_tags
from ankihub.main.note_conversion import (
    ADDON_INTERNAL_TAGS,
    TAG_FOR_OPTIONAL_TAGS,
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    _get_fields_protected_by_tags,
)
from ankihub.main.note_type_management import add_note_type_fields
from ankihub.main.review_data import (
    _get_first_and_last_review_datetime_for_ah_deck,
    _get_review_count_for_ah_deck_since,
    get_daily_review_summaries_since_last_sync,
    send_daily_review_summaries,
    send_review_data,
)
from ankihub.main.subdecks import (
    SUBDECK_TAG,
    add_subdeck_tags_to_notes,
    deck_contains_subdeck_tags,
)
from ankihub.main.suggestions import ChangeSuggestionResult
from ankihub.main.utils import (
    ANKIHUB_CSS_END_COMMENT,
    ANKIHUB_HTML_END_COMMENT,
    Resource,
    clear_empty_cards,
    exclude_descendant_decks,
    get_original_dids_for_nids,
    lowest_level_common_ancestor_deck_name,
    mh_tag_to_resource,
    mids_of_notes,
    note_type_name_without_ankihub_modifications,
    note_type_with_updated_templates_and_css,
    retain_nids_with_ah_note_type,
)
from ankihub.settings import (
    ANKIWEB_ID,
    BehaviorOnRemoteNoteDeleted,
    DatadogLogHandler,
    config,
    log_file_path,
)


@pytest.fixture
def ankihub_db() -> Generator[_AnkiHubDB, None, None]:
    with tempfile.TemporaryDirectory() as temp_dir:
        db = _AnkiHubDB()
        db_path = Path(temp_dir) / "ankihub.db"
        db.setup_and_migrate(db_path)
        yield db


class TestUploadMediaForSuggestion:
    def test_update_media_names_on_notes(self, anki_session_with_addon_data: AnkiSession):
        with anki_session_with_addon_data.profile_loaded():
            mw = anki_session_with_addon_data.mw

            note_contents = [
                'Sample Text <div> abc <img src="test.png"> </div>',
                "<span> a</span><img src='other_test.gif' width='250'><div></div>",
                '<span> <p>this note will not have its image replaced </p> <img src="will_not_replace.jpeg"> </span>',
            ]

            notes: List[Note] = []
            mw.col.decks.add_normal_deck_with_name("MediaTestDeck")
            for content in note_contents:
                note = mw.col.new_note(mw.col.models.by_name("Basic"))
                notes.append(note)
                note["Front"] = content
                mw.col.add_note(note, mw.col.decks.by_name("MediaTestDeck")["id"])

            hashed_name_map = {
                "test.png": "fueriwhfvureivhnaowuyiegrofuaywwqg.png",
                "other_test.gif": "fWJKERDVNMOWIKJCIWJefgjnverf.gif",
            }

            suggestions._update_media_names_on_notes(hashed_name_map)

            notes[0].load()
            notes[1].load()
            notes[2].load()

            assert f'<img src="{hashed_name_map["test.png"]}">' in " ".join(notes[0].fields)
            assert f"<img src='{hashed_name_map['other_test.gif']}' width='250'>" in " ".join(notes[1].fields)
            assert '<img src="will_not_replace.jpeg">' in " ".join(notes[2].fields)


class TestMediaSyncMediaDownload:
    def test_with_exception(self, mocker: MockerFixture, qtbot: QtBot):
        update_and_download_mock = mocker.patch.object(
            media_sync,
            "_update_deck_media_and_download_missing_media",
            side_effect=Exception("test"),
        )

        with qtbot.captureExceptions() as exceptions:
            media_sync.start_media_download()
            qtbot.wait(500)

        # Assert that _download_in_progress was set to False and the exception was raised
        assert not media_sync._download_in_progress
        assert len(exceptions) == 1
        update_and_download_mock.assert_called_once()  # sanity check


class TestMediaSyncMediaUpload:
    def test_with_exception(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        qtbot: QtBot,
        next_deterministic_uuid,
    ):
        with anki_session_with_addon_data.profile_loaded():
            upload_media_mock = mocker.patch.object(
                media_sync._client,
                "upload_media",
                side_effect=Exception("test"),
            )

            with qtbot.captureExceptions() as exceptions:
                media_sync.start_media_upload([], next_deterministic_uuid())
                qtbot.wait(500)

            # Assert that _amount_uploads_in_progress was was reset to 0 and the exception was raised
            assert media_sync._amount_uploads_in_progress == 0
            assert len(exceptions) == 1
            upload_media_mock.assert_called_once()  # sanity check


def test_lowest_level_common_ancestor_deck_name():
    deck_names = [
        "A",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A"

    deck_names = [
        "A::B::C",
        "A::B::C::D",
        "A::B",
    ]
    assert lowest_level_common_ancestor_deck_name(deck_names) == "A::B"

    deck_names = ["A::B::C", "A::B::C::D", "A::B", "B"]
    assert lowest_level_common_ancestor_deck_name(deck_names) is None


def test_updated_tags():
    assert set(
        _updated_tags(
            cur_tags=[],
            incoming_tags=["A", "B"],
            protected_tags=[],
        )
    ) == set(["A", "B"])

    # dont delete protected tags
    assert set(
        _updated_tags(
            cur_tags=["A", "B"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A"])

    # ... the comparison should be case insensitive
    assert set(
        _updated_tags(
            cur_tags=["a", "B"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["a"])

    # dont delete tags that contain protected tags
    assert set(
        _updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["A"],
        )
    ) == set(["A::B::C"])

    assert set(
        _updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["B"],
        )
    ) == set(["A::B::C"])

    assert set(
        _updated_tags(
            cur_tags=["A::B::C"],
            incoming_tags=[],
            protected_tags=["C"],
        )
    ) == set(["A::B::C"])

    # ... the comparison should be case insensitive
    assert set(
        _updated_tags(
            cur_tags=["a::b::c"],
            incoming_tags=[],
            protected_tags=["C"],
        )
    ) == set(["a::b::c"])

    # keep add-on internal tags
    assert set(
        _updated_tags(
            cur_tags=ADDON_INTERNAL_TAGS,
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(ADDON_INTERNAL_TAGS)

    # keep Anki internal tags
    assert set(
        _updated_tags(
            cur_tags=["marked", "leech"],
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set(["marked", "leech"])

    # keep optional tags
    optional_tag = f"{TAG_FOR_OPTIONAL_TAGS}::tag_group::tag"
    assert set(
        _updated_tags(
            cur_tags=[optional_tag],
            incoming_tags=[],
            protected_tags=[],
        )
    ) == set([optional_tag])


def test_mids_of_notes(anki_session: AnkiSession):
    with anki_session.profile_loaded():
        mw = anki_session.mw

        # Create a basic note
        basic = mw.col.models.by_name("Basic")
        note_basic = mw.col.new_note(basic)
        mw.col.add_note(note_basic, deck_id=DeckId(1))

        # Create a cloze note
        cloze = mw.col.models.by_name("Cloze")
        note_cloze = mw.col.new_note(cloze)
        mw.col.add_note(note_cloze, deck_id=DeckId(1))

        # Assert that getting the note type ids of the two notes works
        assert mids_of_notes([note_basic.id, note_cloze.id]) == {
            note_basic.mid,
            note_cloze.mid,
        }


class TestExcludeDescendantDecks:
    def test_empty_list(self, anki_session: AnkiSession):
        with anki_session.profile_loaded():
            assert exclude_descendant_decks([]) == []

    def test_parent_child_relationship(self, anki_session: AnkiSession):
        with anki_session.profile_loaded():
            parent = aqt.mw.col.decks.add_normal_deck_with_name("Parent")
            child = aqt.mw.col.decks.add_normal_deck_with_name("Parent::Child")
            grandchild = aqt.mw.col.decks.add_normal_deck_with_name("Parent::Child::Grandchild")

            # Only parent should remain when all are in list
            result = exclude_descendant_decks([DeckId(parent.id), DeckId(child.id), DeckId(grandchild.id)])
            assert result == [DeckId(parent.id)]

            # Child should remain when parent not in list
            result = exclude_descendant_decks([DeckId(child.id), DeckId(grandchild.id)])
            assert result == [child.id]

    def test_multiple_hierarchies(self, anki_session: AnkiSession):
        with anki_session.profile_loaded():
            deck1 = aqt.mw.col.decks.add_normal_deck_with_name("Deck1")
            child1 = aqt.mw.col.decks.add_normal_deck_with_name("Deck1::Child1")
            deck2 = aqt.mw.col.decks.add_normal_deck_with_name("Deck2")
            child2 = aqt.mw.col.decks.add_normal_deck_with_name("Deck2::Child2")

            result = exclude_descendant_decks(
                [
                    DeckId(deck1.id),
                    DeckId(child1.id),
                    DeckId(deck2.id),
                    DeckId(child2.id),
                ]
            )
            assert set(result) == {DeckId(deck1.id), DeckId(deck2.id)}


class TestGetOriginalDidsForNids:
    def test_notes_in_regular_decks(self, anki_session: AnkiSession, add_anki_note: AddAnkiNote):
        with anki_session.profile_loaded():
            # Create notes
            note1 = add_anki_note()
            note2 = add_anki_note()

            # Set did values (odid should be 0 for regular decks)
            aqt.mw.col.db.execute(f"UPDATE cards SET did = 100, odid = 0 WHERE nid = {note1.id}")
            aqt.mw.col.db.execute(f"UPDATE cards SET did = 200, odid = 0 WHERE nid = {note2.id}")

            result = get_original_dids_for_nids([note1.id, note2.id])
            assert result == {DeckId(100), DeckId(200)}

    def test_notes_in_filtered_deck(self, anki_session: AnkiSession, add_anki_note: AddAnkiNote):
        with anki_session.profile_loaded():
            # Create a note
            note = add_anki_note()

            # Simulate card being in a filtered deck (odid = original deck, did = filtered deck)
            aqt.mw.col.db.execute(f"UPDATE cards SET did = 999, odid = 123 WHERE nid = {note.id}")

            # Should return the original deck ID (odid), not the filtered deck ID (did)
            result = get_original_dids_for_nids([note.id])
            assert result == {DeckId(123)}


class TestGetFieldsProtectedByTags:
    @pytest.mark.parametrize(
        "tags,field_names,expected_protected_fields",
        [
            ([], ["Text", "Extra"], []),
            (
                [f"{TAG_FOR_PROTECTING_FIELDS}::Text"],
                ["Text", "Extra"],
                ["Text"],
            ),
            (
                [
                    f"{TAG_FOR_PROTECTING_FIELDS}::Text",
                    f"{TAG_FOR_PROTECTING_FIELDS}::Extra",
                ],
                ["Text", "Extra"],
                ["Text", "Extra"],
            ),
            # For fields with spaces in their names a tag with spaces replaced by underscores will protect the field
            (
                [f"{TAG_FOR_PROTECTING_FIELDS}::Missed_Questions"],
                ["Text", "Missed Questions"],
                ["Missed Questions"],
            ),
            # The tag comparison is case insensitive
            (
                [f"{TAG_FOR_PROTECTING_FIELDS}::Missed_Questions".lower()],
                ["Text", "Missed Questions"],
                ["Missed Questions"],
            ),
            # Test the tag for protecting all fields
            (
                [TAG_FOR_PROTECTING_ALL_FIELDS],
                ["Text", "Extra", "Missed Questions", "Lecture Notes"],
                ["Text", "Extra", "Missed Questions", "Lecture Notes"],
            ),
            (
                [TAG_FOR_PROTECTING_ALL_FIELDS.lower()],
                ["Text", "Extra", "Missed Questions", "Lecture Notes"],
                ["Text", "Extra", "Missed Questions", "Lecture Notes"],
            ),
        ],
    )
    def test_protect_fields(
        self,
        tags: List[str],
        field_names: List[str],
        expected_protected_fields: List[str],
    ):
        assert set(
            _get_fields_protected_by_tags(
                tags=tags,
                field_names=field_names,
            )
        ) == set(expected_protected_fields)

    def test_try_to_protect_not_existing_field(self):
        # When trying to protect a field that does not exist, it should be ignored.
        assert set(
            _get_fields_protected_by_tags(
                tags=[
                    f"{TAG_FOR_PROTECTING_FIELDS}::Text",
                    f"{TAG_FOR_PROTECTING_FIELDS}::Front",
                ],
                field_names=["Text", "Extra", "Missed Questions", "Lecture Notes"],
            )
        ) == set(["Text"])


def test_normalize_url():
    url = "https://app.ankihub.net/api/decks/fc39e7e7-9705-4102-a6ec-90d128c64ed3/updates?since=2022-08-01T1?6%3A32%3A2"
    assert _normalize_url(url) == "https://app.ankihub.net/api/decks/<id>/updates"

    url = "https://app.ankihub.net/api/note-types/2385223452/"
    assert _normalize_url(url) == "https://app.ankihub.net/api/note-types/<id>/"


def test_prepared_field_html():
    assert _prepared_field_html('<img src="foo.jpg">') == '<img src="foo.jpg">'

    assert _prepared_field_html('<img src="foo.jpg" data-editor-shrink="true">') == '<img src="foo.jpg">'


def test_remove_note_type_name_modifications():
    name = "Basic (deck_name / user_name)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name / user_name) (deck_name2 / user_name2)"
    assert note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name/user_name)"
    assert note_type_name_without_ankihub_modifications(name) == name


class TestDeckContainsSubdeckTags:
    def test_with_subdeck_tags(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            note_info = NoteInfoFactory.create(tags=[f"{SUBDECK_TAG}::A::B"])
            import_ah_note(ah_did=ah_did, note_data=note_info)

            assert deck_contains_subdeck_tags(ah_did)

    def test_without_subdeck_tags(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            import_ah_note(ah_did=ah_did)

            assert not deck_contains_subdeck_tags(ah_did)

    def test_with_multiple_notes_and_tags(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()

            import_ah_note(ah_did=ah_did)

            note_info = NoteInfoFactory.create(tags=["some_other_tag", f"{SUBDECK_TAG}::A::B"])
            import_ah_note(ah_did=ah_did, note_data=note_info)

            assert deck_contains_subdeck_tags(ah_did)


def test_add_subdeck_tags_to_notes(anki_session_with_addon_data: AnkiSession):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        mw.col.decks.add_normal_deck_with_name("A::B::C")

        note1 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note1["Front"] = "note1"
        mw.col.add_note(note1, mw.col.decks.by_name("A")["id"])

        note2 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note2["Front"] = "note2"
        mw.col.add_note(note2, mw.col.decks.by_name("A::B")["id"])

        note3 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note3["Front"] = "note3"
        mw.col.add_note(note3, mw.col.decks.by_name("A::B::C")["id"])

        add_subdeck_tags_to_notes("A", ankihub_deck_name="Test")

        note1.load()
        assert note1.tags == [f"{SUBDECK_TAG}::Test"]

        note2.load()
        assert note2.tags == [f"{SUBDECK_TAG}::Test::B"]

        note3.load()
        assert note3.tags == [f"{SUBDECK_TAG}::Test::B::C"]


def test_add_subdeck_tags_to_notes_with_spaces_in_deck_name(
    anki_session_with_addon_data: AnkiSession,
):
    with anki_session_with_addon_data.profile_loaded():
        mw = anki_session_with_addon_data.mw

        mw.col.decks.add_normal_deck_with_name(" a a :: b b :: c c ")

        note1 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note1["Front"] = "note1"
        mw.col.add_note(note1, mw.col.decks.by_name(" a a ")["id"])

        note2 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note2["Front"] = "note2"
        mw.col.add_note(note2, mw.col.decks.by_name(" a a :: b b ")["id"])

        note3 = mw.col.new_note(mw.col.models.by_name("Basic"))
        note3["Front"] = "note3"
        mw.col.add_note(note3, mw.col.decks.by_name(" a a :: b b :: c c ")["id"])

        add_subdeck_tags_to_notes(" a a ", ankihub_deck_name="AA")

        note1.load()
        assert note1.tags == [f"{SUBDECK_TAG}::AA"]

        note2.load()
        assert note2.tags == [f"{SUBDECK_TAG}::AA::b_b"]

        note3.load()
        assert note3.tags == [f"{SUBDECK_TAG}::AA::b_b::c_c"]


class TestAnkiHubSignOut:
    @pytest.mark.parametrize("confirmed_sign_out,expected_logged_in_state", [(True, False), (False, True)])
    def test_sign_out(
        self,
        monkeypatch: MonkeyPatch,
        anki_session_with_addon_data: AnkiSession,
        requests_mock: Mocker,
        confirmed_sign_out: bool,
        expected_logged_in_state: bool,
    ):
        anki_session = anki_session_with_addon_data

        with anki_session.profile_loaded():
            user_token = "random_token_382fasfkjep1flaksnioqwndjk&@*(%248)"
            # This means user is logged in
            config.save_token(user_token)

            mw = anki_session.mw
            menu_state.ankihub_menu = QMenu("&AnkiHub", parent=aqt.mw)
            mw.form.menubar.addMenu(menu_state.ankihub_menu)
            setup_config_dialog_manager()
            refresh_ankihub_menu()

            sign_out_action = [
                action for action in menu_state.ankihub_menu.actions() if action.text() == "🔑 Sign out"
            ][0]

            assert sign_out_action is not None

            ask_user_mock = Mock(return_value=confirmed_sign_out)
            monkeypatch.setattr(menu, "ask_user", ask_user_mock)

            requests_mock.post(f"{DEFAULT_API_URL}/logout/", status_code=204, json=[])

            sign_out_action.trigger()

            ask_user_mock.assert_called_once_with(
                "Are you sure you want to Sign out?",
                yes_button_label="Sign Out",
                no_button_label="Cancel",
            )

            if expected_logged_in_state:
                expected_token = user_token
            else:
                expected_token = ""

            assert config.token() == expected_token
            assert config.is_logged_in() is expected_logged_in_state


class TestAnkiHubLoginDialog:
    def test_login(
        self,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        username = "test_username"
        password = "test_password"
        token = "test_token"

        login_mock = mocker.patch("ankihub.gui.menu.AnkiHubClient.login", return_value=token)

        AnkiHubLogin.display_login()

        window: AnkiHubLogin = AnkiHubLogin._window

        window.username_or_email_box_text.setText(username)
        window.password_box_text.setText(password)
        window.login_button.click()

        qtbot.wait_until(lambda: not window.isVisible())

        login_mock.assert_called_once_with(credentials={"username": username, "password": password})

        assert config.user() == username
        assert config.token() == token

    @patch("ankihub.gui.menu.AnkiHubClient.login")
    def test_password_visibility_toggle(self, login_mock, qtbot: QtBot):
        password = "test_password"

        AnkiHubLogin.display_login()

        window: AnkiHubLogin = AnkiHubLogin._window
        window.password_box_text.setText(password)

        # assert password is not visible and toggle button is at the initial state
        assert window.password_box_text.echoMode() == QLineEdit.EchoMode.Password
        assert window.toggle_button.isChecked() is False

        window.toggle_button.click()
        qtbot.wait_until(lambda: window.password_box_text.echoMode() == QLineEdit.EchoMode.Normal)

        assert window.password_box_text.echoMode() == QLineEdit.EchoMode.Normal
        assert window.toggle_button.isChecked() is True

        window.toggle_button.click()
        qtbot.wait_until(lambda: window.password_box_text.echoMode() == QLineEdit.EchoMode.Password)

        assert window.password_box_text.echoMode() == QLineEdit.EchoMode.Password
        assert window.toggle_button.isChecked() is False

    @patch("ankihub.gui.menu.AnkiHubClient.login")
    def test_forgot_password_and_sign_up_links_are_present(self, login_mock, qtbot: QtBot):
        AnkiHubLogin.display_login()

        window: AnkiHubLogin = AnkiHubLogin._window

        assert window.sign_up_help_text.openExternalLinks() is True
        assert (
            window.sign_up_help_text.text()
            == 'Don\'t have an AnkiHub account? <a href="https://app.ankihub.net/accounts/signup/">Register now</a>'
        )

        assert window.recover_password_help_text.openExternalLinks() is True
        assert (
            window.recover_password_help_text.text()
            == '<a href="https://app.ankihub.net/accounts/password/reset/">Forgot password?</a>'
        )


class TestSuggestionDialog:
    @pytest.mark.parametrize(
        "is_new_note_suggestion,is_for_anking_deck,suggestion_type,source_type,media_was_added",
        [
            (True, True, SuggestionType.NEW_CONTENT, SourceType.AMBOSS, False),
            (True, True, SuggestionType.OTHER, SourceType.AMBOSS, False),
            (True, False, SuggestionType.NEW_CONTENT, SourceType.AMBOSS, False),
            (True, False, SuggestionType.OTHER, SourceType.AMBOSS, False),
            (False, True, SuggestionType.NEW_CONTENT, SourceType.AMBOSS, False),
            (False, True, SuggestionType.OTHER, SourceType.AMBOSS, False),
            (False, False, SuggestionType.NEW_CONTENT, SourceType.AMBOSS, False),
            (False, False, SuggestionType.OTHER, SourceType.AMBOSS, False),
            (False, True, SuggestionType.NEW_CONTENT, SourceType.UWORLD, False),
            (False, True, SuggestionType.NEW_CONTENT, SourceType.UWORLD, True),
            (False, True, SuggestionType.DELETE, SourceType.DUPLICATE_NOTE, False),
            (False, False, SuggestionType.DELETE, SourceType.DUPLICATE_NOTE, False),
        ],
    )
    def test_visibility_of_form_elements_and_form_result(
        self,
        is_new_note_suggestion: bool,
        is_for_anking_deck: bool,
        suggestion_type: SuggestionType,
        source_type: SourceType,
        media_was_added: bool,
        qtbot: QtBot,
        mocker: MockerFixture,
    ):
        callback_mock = mocker.stub()
        dialog = SuggestionDialog(
            is_for_anking_deck=is_for_anking_deck,
            is_new_note_suggestion=is_new_note_suggestion,
            added_new_media=media_was_added,
            can_submit_without_review=True,
            callback=callback_mock,
        )
        dialog.show()

        # Fill in the form
        change_type_needed = not is_new_note_suggestion
        source_needed = not is_new_note_suggestion and (
            (suggestion_type in [SuggestionType.UPDATED_CONTENT, SuggestionType.NEW_CONTENT] and is_for_anking_deck)
            or suggestion_type == SuggestionType.DELETE
        )

        if change_type_needed:
            dialog.change_type_select.setCurrentText(suggestion_type.value[1])

        expected_source_text = ""
        if source_needed:
            dialog.source_widget.source_type_select.setCurrentText(source_type.value)
            if source_type == SourceType.UWORLD:
                expected_uworld_step = "Step 1"
                dialog.source_widget.uworld_step_select.setCurrentText(expected_uworld_step)

            expected_source_text = "https://test_url.com"
            dialog.source_widget.source_edit.setText(expected_source_text)

        dialog.rationale_edit.setPlainText("test")

        # Assert that correct form elements are shown
        assert dialog.isVisible()
        assert dialog.change_type_select.isVisible() == change_type_needed
        assert dialog.source_widget_group_box.isVisible() == source_needed
        assert dialog.hint_for_note_deletions.isVisible() == (suggestion_type == SuggestionType.DELETE)

        # Assert that the form submit button is enabled (it is disabled if the form input is invalid)
        assert dialog.button_box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

        # Assert that the form result is correct
        expected_source_text = (
            f"{expected_uworld_step} {expected_source_text}"
            if source_type == SourceType.UWORLD
            else expected_source_text
        )
        expected_source = (
            SuggestionSource(source_type=source_type, source_text=expected_source_text) if source_needed else None
        )

        dialog.accept()

        qtbot.wait_until(lambda: callback_mock.called)

        callback_mock.assert_called_once_with(
            SuggestionMetadata(
                comment="test",
                change_type=suggestion_type if change_type_needed else None,
                source=expected_source,
            )
        )

    @pytest.mark.parametrize(
        "can_submit_without_review",
        [
            True,
            False,
        ],
    )
    def test_submit_without_review_checkbox(self, can_submit_without_review: bool, mocker: MockerFixture):
        callback_mock = mocker.stub()
        dialog = SuggestionDialog(
            is_for_anking_deck=False,
            is_new_note_suggestion=False,
            added_new_media=False,
            can_submit_without_review=can_submit_without_review,
            callback=callback_mock,
        )
        dialog.show()

        assert dialog.auto_accept_cb.isVisible() == can_submit_without_review


class TestSuggestionDialogGetAnkiNidToAHDidsDict:
    def test_with_existing_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            note_info = import_ah_note(ah_did=ah_did)
            nids = [NoteId(note_info.anki_nid)]
            assert get_anki_nid_to_ah_dids_dict(nids) == {note_info.anki_nid: ah_did}

    def test_with_new_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            note_type = import_ah_note_type(ah_did=ah_did)
            note = add_anki_note(note_type=note_type)
            nids = [note.id]
            assert get_anki_nid_to_ah_dids_dict(nids) == {note.id: ah_did}


class MockDependenciesForSuggestionDialog(Protocol):
    def __call__(self, user_cancels: bool) -> Tuple[Mock, Mock]: ...


@pytest.fixture
def mock_dependiencies_for_suggestion_dialog(
    mocker: MockerFixture,
    mock_suggestion_dialog,
) -> MockDependenciesForSuggestionDialog:
    """Mocks the dependencies for open_suggestion_dialog_for_note.
    Returns a tuple of mocks that replace suggest_note_update and suggest_new_note
    If user_cancels is True, SuggestionDialog.run behaves as if the user cancelled the dialog.
    """

    def mock_dependencies_for_suggestion_dialog_inner(
        user_cancels: bool,
    ) -> Tuple[Mock, Mock]:
        mock_suggestion_dialog(user_cancels=user_cancels)

        suggest_note_update_mock = mocker.patch("ankihub.gui.suggestion_dialog.suggest_note_update")
        suggest_new_note_mock = mocker.patch("ankihub.gui.suggestion_dialog.suggest_new_note")

        return suggest_note_update_mock, suggest_new_note_mock

    return mock_dependencies_for_suggestion_dialog_inner


class TestOpenSuggestionDialogForSingleSuggestion:
    @pytest.mark.parametrize(
        "user_cancels, suggest_note_update_succeeds",
        [
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ],
    )
    def test_with_existing_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        mock_dependiencies_for_suggestion_dialog: MockDependenciesForSuggestionDialog,
        install_ah_deck: InstallAHDeck,
        user_cancels: bool,
        suggest_note_update_succeeds: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)
            note = aqt.mw.col.get_note(NoteId(note_info.anki_nid))

            (
                suggest_note_update_mock,
                suggest_new_note_mock,
            ) = mock_dependiencies_for_suggestion_dialog(user_cancels=user_cancels)

            suggest_note_update_mock.return_value = (
                ChangeSuggestionResult.SUCCESS if suggest_note_update_succeeds else ChangeSuggestionResult.NO_CHANGES
            )

            open_suggestion_dialog_for_single_suggestion(note=note, parent=aqt.mw)

            if user_cancels:
                suggest_note_update_mock.assert_not_called()
                suggest_new_note_mock.assert_not_called()
            else:
                _, kwargs = suggest_note_update_mock.call_args
                assert kwargs.get("note") == note

                suggest_new_note_mock.assert_not_called()

    def test_with_new_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        mock_dependiencies_for_suggestion_dialog: MockDependenciesForSuggestionDialog,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_type = import_ah_note_type(ah_did=ah_did_1)

            note = add_anki_note(note_type=note_type)

            (
                suggest_note_update_mock,
                suggest_new_note_mock,
            ) = mock_dependiencies_for_suggestion_dialog(user_cancels=False)

            open_suggestion_dialog_for_single_suggestion(note=note, parent=aqt.mw)

            _, kwargs = suggest_new_note_mock.call_args
            assert kwargs.get("note") == note

            suggest_note_update_mock.assert_not_called()


class MockDependenciesForBulkSuggestionDialog(Protocol):
    def __call__(self, user_cancels: bool) -> Mock: ...


@pytest.fixture
def mock_dependencies_for_bulk_suggestion_dialog(
    mock_suggestion_dialog: MockSuggestionDialog,
    mocker: MockerFixture,
) -> MockDependenciesForBulkSuggestionDialog:
    """Mocks the dependencies for open_suggestion_dialog_for_bulk_suggestion.
    Returns a Mock that replaces suggest_notes_in_bulk.
    If user_cancels is True, SuggestionDialog.run behaves as if the user cancelled the dialog.
    """

    def mock_dependencies_for_suggestion_dialog_inner(user_cancels: bool) -> Mock:
        mock_suggestion_dialog(user_cancels=user_cancels)

        suggest_notes_in_bulk_mock = mocker.patch(
            "ankihub.gui.suggestion_dialog.suggest_notes_in_bulk",
        )

        mocker.patch("ankihub.gui.suggestion_dialog._on_suggest_notes_in_bulk_done")
        return suggest_notes_in_bulk_mock

    return mock_dependencies_for_suggestion_dialog_inner


class TestOpenSuggestionDialogForBulkSuggestion:
    @pytest.mark.parametrize(
        "user_cancels",
        [
            True,
            False,
        ],
    )
    def test_with_existing_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        qtbot: QtBot,
        mock_dependencies_for_bulk_suggestion_dialog: MockDependenciesForBulkSuggestionDialog,
        user_cancels: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)
            nids = [NoteId(note_info.anki_nid)]

            suggest_notes_in_bulk_mock = mock_dependencies_for_bulk_suggestion_dialog(user_cancels=user_cancels)

            open_suggestion_dialog_for_bulk_suggestion(anki_nids=nids, parent=aqt.mw)

            if user_cancels:
                qtbot.wait(500)
                suggest_notes_in_bulk_mock.assert_not_called()
            else:
                qtbot.wait_until(lambda: suggest_notes_in_bulk_mock.called)
                _, kwargs = suggest_notes_in_bulk_mock.call_args
                assert kwargs.get("ankihub_did") == ah_did
                assert {note.id for note in kwargs.get("notes")} == set(nids)

    def test_with_two_new_notes_from_different_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        mock_dependencies_for_bulk_suggestion_dialog: MockDependenciesForBulkSuggestionDialog,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_type_1 = import_ah_note_type(ah_did=ah_did_1, force_new=True)
            note_1 = add_anki_note(note_type=note_type_1)

            ah_did_2 = install_ah_deck()
            note_type_2 = import_ah_note_type(ah_did=ah_did_2, force_new=True)
            note_2 = add_anki_note(note_type=note_type_2)

            nids = [note_1.id, note_2.id]

            suggest_notes_in_bulk_mock = mock_dependencies_for_bulk_suggestion_dialog(user_cancels=False)

            open_suggestion_dialog_for_bulk_suggestion(anki_nids=nids, parent=aqt.mw)
            qtbot.wait(500)

            # No suggestion should be created, because the notes need to belong to the same deck.
            suggest_notes_in_bulk_mock.assert_not_called()


class TestOnSuggestNotesInBulkDone:
    def test_correct_message_is_shown(
        self,
        mocker: MockerFixture,
    ):
        showText_mock = mocker.patch("ankihub.gui.suggestion_dialog.showText")
        nid_1 = NoteId(1)
        nid_2 = NoteId(2)
        nid_3 = NoteId(3)
        _on_suggest_notes_in_bulk_done(
            future=future_with_result(
                suggestions.BulkNoteSuggestionsResult(
                    errors_by_nid={
                        nid_1: ["some error"],
                        nid_2: [suggestions.ANKIHUB_NO_CHANGE_ERROR],
                        nid_3: ["Note object does not exist"],
                    },
                    change_note_suggestions_count=10,
                    new_note_suggestions_count=20,
                )
            ),
            parent=aqt.mw,
        )

        _, kwargs = showText_mock.call_args
        assert (
            kwargs.get("txt")
            == dedent(
                """
                Submitted 10 change note suggestion(s).
                Submitted 20 new note suggestion(s).


                Failed to submit suggestions for 3 note(s).
                All notes with failed suggestions:
                1, 2, 3

                Notes without changes (1):
                2

                Notes that don't exist on AnkiHub (1):
                3
                """
            ).strip()
        )

    def test_with_exception_in_future(self):
        with pytest.raises(Exception):
            _on_suggest_notes_in_bulk_done(
                future=future_with_exception(Exception("test")),
                parent=aqt.mw,
            )

    def test_with_http_403_exception_in_future(self, mocker: MockerFixture):
        response = Response()
        response.status_code = 403
        response.json = lambda: {"detail": "test"}  # type: ignore
        exception = AnkiHubHTTPError(response)

        show_error_dialog_mock = mocker.patch(
            "ankihub.gui.suggestion_dialog.show_error_dialog",
        )

        _on_suggest_notes_in_bulk_done(
            future=future_with_exception(exception),
            parent=aqt.mw,
        )
        _, kwargs = show_error_dialog_mock.call_args
        assert kwargs.get("message") == "test"


class TestAnkiHubDBAnkiHubNidsToAnkiIds:
    def test_ankihub_nids_to_anki_ids(
        self,
        ankihub_db: _AnkiHubDB,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        ah_did = next_deterministic_uuid()
        anki_nid = 1

        ankihub_db.upsert_note_type(
            ankihub_did=ah_did,
            note_type=ankihub_basic_note_type,
        )

        existing_ah_nid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            anki_nid=anki_nid,
            ah_nid=existing_ah_nid,
            mid=ankihub_basic_note_type["id"],
        )
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        not_existing_ah_nid = next_deterministic_uuid()

        # Retrieve a dict of anki_nid -> ah_nid for two anki_nids.
        ah_nids_for_anki_nids = ankihub_db.ankihub_nids_to_anki_nids(
            ankihub_nids=[existing_ah_nid, not_existing_ah_nid]
        )

        assert ah_nids_for_anki_nids == {
            existing_ah_nid: anki_nid,
            not_existing_ah_nid: None,
        }


class TestAnkiHubDBRemoveNotes:
    def test_remove_notes(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        ankihub_basic_note_type: NotetypeDict,
    ):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_note_type(
            ankihub_did=ah_did,
            note_type=ankihub_basic_note_type,
        )

        ah_nid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            ah_nid=ah_nid,
            mid=ankihub_basic_note_type["id"],
        )
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        assert ankihub_db.anki_nids_for_ankihub_deck(ah_did) == [note.anki_nid]

        ankihub_db.remove_notes(
            ah_nids=[ah_nid],
        )

        assert ankihub_db.anki_nids_for_ankihub_deck(ankihub_did=ah_did) == []


class TestAnkiHubDBRemoveDeck:
    def test_removes_notes_and_note_types_and_deck_media(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        ankihub_basic_note_type: NotetypeDict,
    ):
        # Add data to the DB.
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_note_type(
            ankihub_did=ah_did,
            note_type=ankihub_basic_note_type,
        )

        ah_nid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            ah_nid=ah_nid,
            mid=ankihub_basic_note_type["id"],
        )
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        # sanity check
        assert ankihub_db.anki_nids_for_ankihub_deck(ah_did) == [note.anki_nid]
        assert len(ankihub_db.note_types_for_ankihub_deck(ah_did))

        deck_media = DeckMediaFactory.create(
            referenced_on_accepted_note=True,
            exists_on_s3=True,
            download_enabled=True,
        )
        ankihub_db.upsert_deck_media_infos(ankihub_did=ah_did, media_list=[deck_media])
        # sanity check
        assert len(ankihub_db.downloadable_media_for_ankihub_deck(ah_did)) == 1

        # Remove the deck
        ankihub_db.remove_deck(ankihub_did=ah_did)

        # Assert that everything is removed
        assert ankihub_db.anki_nids_for_ankihub_deck(ankihub_did=ah_did) == []
        assert ankihub_db.note_types_for_ankihub_deck(ankihub_did=ah_did) == []
        assert ankihub_db.note_type_dict(ankihub_basic_note_type["id"]) is None
        assert not (ankihub_db.ankihub_did_for_note_type(anki_note_type_id=ankihub_basic_note_type["id"]))

        assert ankihub_db.downloadable_media_for_ankihub_deck(ah_did) == []


class TestAnkiHubDBIntegrityError:
    def test_upserting_notes_without_note_type_raises_integrity_error(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        ankihub_basic_note_type: NotetypeDict,
    ):
        ah_did = next_deterministic_uuid()
        ah_nid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            ah_nid=ah_nid,
            mid=ankihub_basic_note_type["id"],
        )

        with pytest.raises(IntegrityError):
            ankihub_db.upsert_notes_data(
                ankihub_did=ah_did,
                notes_data=[note],
            )


class TestAnkiHubDBMissingValueError:
    def test_getting_note_data_raises_error_when_fields_is_none(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        ankihub_basic_note_type: NotetypeDict,
    ):
        ah_did = next_deterministic_uuid()

        # Add a note to the DB and set fields to None
        anki_nid = 1
        ankihub_db.upsert_note_type(ah_did, ankihub_basic_note_type)
        note = NoteInfoFactory.create(anki_nid=anki_nid, mid=ankihub_basic_note_type["id"])
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )
        ankihub_db.db.execute_sql("UPDATE notes SET fields = NULL")

        with pytest.raises(MissingValueError) as exc_info:
            ankihub_db.note_data(anki_nid)

        assert exc_info.value.ah_did == ah_did


class TestAnkiHubDBMediaNamesForAnkiHubDeck:
    @pytest.fixture(autouse=True)
    def setup_method_fixture(
        self,
        ankihub_db: _AnkiHubDB,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        self.mid = ankihub_basic_note_type["id"]
        note_info = NoteInfoFactory.create(
            mid=self.mid,
            fields=[
                Field(value="test <img src='test1.jpg'>", name="Front"),
                Field(
                    value="test <img src='test2.jpg'> [sound:test3.mp3]",
                    name="Back",
                ),
            ],
        )
        self.ah_did = next_deterministic_uuid()
        ankihub_db.upsert_note_type(ankihub_did=self.ah_did, note_type=ankihub_basic_note_type)
        ankihub_db.upsert_notes_data(self.ah_did, [note_info])

    def test_basic(
        self,
        anki_session: AnkiSession,
        ankihub_db: _AnkiHubDB,
    ):
        with anki_session.profile_loaded():
            # Assert that the media name is returned for the field that is not disabled
            # and the media name is not returned for the field that is disabled.
            assert ankihub_db.media_names_for_ankihub_deck(self.ah_did) == {
                "test1.jpg",
                "test2.jpg",
                "test3.mp3",
            }


@pytest.mark.parametrize(
    "referenced_on_accepted_note,exists_on_s3,download_enabled",
    [
        (True, True, True),
        (True, True, False),
        (True, False, True),
        (True, False, False),
        (False, True, True),
        (False, True, False),
        (False, False, True),
        (False, False, False),
    ],
)
class TestAnkiHubDBDownloadableMediaNamesForAnkiHubDeck:
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        ankihub_db: _AnkiHubDB,
        referenced_on_accepted_note: bool,
        exists_on_s3: bool,
        download_enabled: bool,
    ):
        media_list = [
            DeckMediaFactory.create(
                name="test1.jpg",
                referenced_on_accepted_note=referenced_on_accepted_note,
                exists_on_s3=exists_on_s3,
                download_enabled=download_enabled,
            )
        ]
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            ankihub_db.upsert_deck_media_infos(
                ankihub_did=ah_did,
                media_list=media_list,
            )

            expected_result = [media_list] if referenced_on_accepted_note and exists_on_s3 and download_enabled else []
            assert ankihub_db.downloadable_media_for_ankihub_deck(ah_did=ah_did) == expected_result


class TestAnkiHubDBMediaNamesWithMatchingHashes:
    def test_get_matching_media(self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash="hash1"),
            ],
        )

        assert ankihub_db.media_names_with_matching_hashes(
            ah_did=ah_did, media_to_hash={"test1_copy.jpg": "hash1"}
        ) == {"test1_copy.jpg": "test1.jpg"}

    def test_get_matching_media_with_multiple_entries(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash="hash1"),
                DeckMediaFactory.create(name="test2.jpg", file_content_hash="hash2"),
            ],
        )

        assert ankihub_db.media_names_with_matching_hashes(
            ah_did=ah_did,
            media_to_hash={
                "test1_copy.jpg": "hash1",
                "test2_copy.jpg": "hash2",
            },
        ) == {
            "test1_copy.jpg": "test1.jpg",
            "test2_copy.jpg": "test2.jpg",
        }

    def test_get_matching_media_with_mixed_entries(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash="hash1"),
                DeckMediaFactory.create(name="test2.jpg", file_content_hash=None),
                DeckMediaFactory.create(name="test3.jpg", file_content_hash="hash3"),
            ],
        )

        assert ankihub_db.media_names_with_matching_hashes(
            ah_did=ah_did,
            media_to_hash={
                "test1_copy.jpg": "hash1",
                "test2_copy.jpg": "hash2",
                "test3_copy.jpg": "hash3",
            },
        ) == {
            "test1_copy.jpg": "test1.jpg",
            "test3_copy.jpg": "test3.jpg",
        }

    def test_with_none_in_media_to_hash(self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash="hash1"),
            ],
        )

        assert ankihub_db.media_names_with_matching_hashes(ah_did=ah_did, media_to_hash={"test1_copy.jpg": None}) == {}

    def test_with_none_in_db(self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash=None),
            ],
        )

        assert (
            ankihub_db.media_names_with_matching_hashes(ah_did=ah_did, media_to_hash={"test1_copy.jpg": "hash1"}) == {}
        )


class TestAnkiHubDBDeckMedia:
    def test_modified_field_is_stored_in_correct_format_in_db(self, ankihub_db: _AnkiHubDB, next_deterministic_uuid):
        ah_did = next_deterministic_uuid()
        deck_media_from_client = DeckMediaFactory.create()

        ankihub_db.upsert_deck_media_infos(ankihub_did=ah_did, media_list=[deck_media_from_client])

        # Assert that the retrieved value is the same as the one that was stored.
        deck_media_from_db = DeckMedia.get()
        assert deck_media_from_db.modified == deck_media_from_client.modified

        # Assert that the modified value is stored in the DB in the correct format.
        cursor = get_peewee_database().execute_sql("SELECT modified from deck_media")
        modified_in_db = cursor.fetchone()[0]
        assert modified_in_db == deck_media_from_client.modified.isoformat()


class TestErrorHandling:
    def test_contains_path_to_this_addon(self):
        # Assert that the function returns True when the input string contains the
        # path to this addon.
        assert _contains_path_to_this_addon("/addons21/ankihub/src/ankihub/errors.py")
        assert _contains_path_to_this_addon(f"/addons21/{ANKIWEB_ID}/src/ankihub/errors.py")

        # Same as above, but with Windows path separators.
        assert _contains_path_to_this_addon("\\addons21\\ankihub\\src\\ankihub\\errors.py")
        assert _contains_path_to_this_addon(f"\\addons21\\{ANKIWEB_ID}\\src\\ankihub\\errors.py")

        # Assert that the function returns False when the input string does not contain
        # the path to this addon.
        assert not _contains_path_to_this_addon("/addons21/other_addon/src/ankihub/errors.py")
        assert not _contains_path_to_this_addon("/addons21/12345789/src/ankihub/errors.py")

        # Same as above, but with Windows path separators.
        assert not _contains_path_to_this_addon("\\addons21\\other_addon\\src\\ankihub\\errors.py")
        assert not _contains_path_to_this_addon("\\addons21\\12345789\\src\\ankihub\\errors.py")

    def test_handle_ankihub_401(self, mocker: MockerFixture):
        # Set up mock for AnkiHub login dialog.
        display_login_mock = mocker.patch.object(AnkiHubLogin, "display_login")

        handled = _try_handle_exception(
            exc_value=AnkiHubHTTPError(response=Mock(status_code=401)),
            tb=None,
        )
        assert handled
        display_login_mock.assert_called_once()

    @pytest.mark.parametrize(
        "response_content, expected_handled",
        [
            # The exception should only be handled for responses with json content that
            # contains the "detail" key.
            ("", False),
            ("{}", False),
            ('{"detail": "test"}', True),
            (f'{{"detail": "{TERMS_AGREEMENT_NOT_ACCEPTED_DETAIL}"}}', True),
        ],
    )
    def test_handle_ankihub_403(
        self,
        mocker: MockerFixture,
        qtbot: QtBot,
        anki_session_with_addon_data: AnkiSession,
        response_content: str,
        expected_handled: bool,
    ):
        show_error_dialog_mock = mocker.patch("ankihub.gui.errors.show_error_dialog")
        terms_and_conditions_dialog = mocker.patch("ankihub.gui.errors.TermsAndConditionsDialog")

        response_mock = mocker.Mock()
        response_mock.status_code = 403
        response_mock.text = response_content
        response_mock.json = lambda: json.loads(response_content)  # type: ignore

        with anki_session_with_addon_data.profile_loaded():
            handled = _try_handle_exception(
                exc_value=AnkiHubHTTPError(response=response_mock),
                tb=None,
            )
            assert handled == expected_handled
            if response_content and json.loads(response_content).get("detail") == TERMS_AGREEMENT_NOT_ACCEPTED_DETAIL:
                qtbot.wait_until(lambda: terms_and_conditions_dialog.display.called)
            else:
                assert show_error_dialog_mock.called == expected_handled

    def test_handle_ankihub_406(self, mocker: MockerFixture):
        ask_user_mock = mocker.patch("ankihub.gui.errors.ask_user", return_value=False)
        handled = _try_handle_exception(
            exc_value=AnkiHubHTTPError(
                response=Mock(
                    status_code=406,
                    json=lambda: {"detail": OUTDATED_CLIENT_RESPONSE_DETAIL},
                )
            ),
            tb=None,
        )
        assert handled
        ask_user_mock.assert_called_once()

    @pytest.mark.parametrize(
        "exception",
        [
            ConnectionError(),
            requests.exceptions.ConnectionError(),
            # wrapped connection erros should be handled as well
            AnkiHubRequestException(original_exception=ConnectionError()),
            DeckDownloadAndInstallError(original_exception=ConnectionError(), ankihub_did=uuid.uuid4()),
        ],
    )
    def test_handle_connection_error(self, exception: Exception, mocker: MockerFixture):
        show_tooltip_mock = mocker.patch("ankihub.gui.errors.show_tooltip")
        handled = _try_handle_exception(
            exc_value=exception,
            tb=None,
        )
        assert handled
        show_tooltip_mock.assert_called_once()

    def test_handle_missing_value_error(self, next_deterministic_uuid):
        ah_did = next_deterministic_uuid()
        config.add_deck(
            ankihub_did=ah_did,
            # The values here are not important for this test.
            name="test_deck",
            anki_did=1,
            user_relation=UserDeckRelation.SUBSCRIBER,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        )

        handled = _try_handle_exception(exc_value=MissingValueError(ah_did=ah_did), tb=None)

        # The exception should be handled, and the deck should be marked for full download.
        assert handled
        assert config.deck_config(ah_did).download_full_deck_on_next_sync


def test_show_error_dialog(anki_session_with_addon_data: AnkiSession, mocker: MockerFixture, qtbot: QtBot):
    with anki_session_with_addon_data.profile_loaded():
        show_dialog_mock = mocker.patch("ankihub.gui.utils.show_dialog")
        show_error_dialog("some message", title="some title", parent=aqt.mw)
        qtbot.wait_until(lambda: show_dialog_mock.called)


class TestUploadLogs:
    def test_basic(self, qtbot: QtBot, mocker: MockerFixture):
        upload_logs_mock = mocker.patch.object(AddonAnkiHubClient, "upload_logs")
        with qtbot.wait_callback() as callback:
            upload_logs_in_background(on_done=callback)

        upload_logs_mock.assert_called_once()
        assert upload_logs_mock.call_args[1]["file"] == log_file_path()

    @pytest.mark.parametrize(
        "exception, expected_report_exception_called",
        [
            # The exception should not be reported for these two specific cases
            (AnkiHubHTTPError(response=Mock(status_code=401)), False),
            (
                AnkiHubHTTPError(response=Mock(status_code=406)),
                False,
            ),
            # The exception should be reported in all other cases
            (AnkiHubHTTPError(response=Mock(status_code=500)), True),
            (Exception("test"), True),
        ],
    )
    def test_with_exception(
        self,
        qtbot: QtBot,
        mocker: MockerFixture,
        expected_report_exception_called: bool,
        exception: Exception,
    ):
        on_done_mock = mocker.stub()
        upload_logs_mock = mocker.patch.object(AddonAnkiHubClient, "upload_logs", side_effect=exception)
        mocker.patch("ankihub.gui.errors._show_feedback_dialog")
        report_exception_mock = mocker.patch("ankihub.gui.errors._report_exception")
        upload_logs_in_background(on_done=on_done_mock)

        qtbot.wait(500)

        upload_logs_mock.assert_called_once()
        on_done_mock.assert_not_called()

        assert report_exception_mock.called == expected_report_exception_called


class TestRateLimitedDecorator:
    def test_rate_limited_decorator(self):
        # Create a counter to keep track of how many times foo is executed
        execution_counter = 0

        @rate_limited(1)
        def foo():
            nonlocal execution_counter
            execution_counter += 1

        for _ in range(11):
            foo()
            time.sleep(0.1)

        # Given the 1-second rate limit and the 11 calls with 0.1-second intervals,
        # we expect foo to be executed 2 times.
        assert execution_counter == 2

    def test_rate_limited_decorator_with_on_done_arg_name(self):
        # Create a counter to keep track of how many times on_done is executed
        execution_counter = 0

        def on_done() -> None:
            nonlocal execution_counter
            execution_counter += 1

        @rate_limited(1, on_done_arg_name="on_done")
        def foo(on_done: Callable[[], None]) -> None:
            on_done()

        for _ in range(11):
            foo(on_done=on_done)
            time.sleep(0.1)

        # The on_done callback should be executed every time foo is called.
        assert execution_counter == 11


def test_error_dialog(qtbot: QtBot, mocker: MockerFixture):
    try:
        raise Exception("test")
    except Exception as e:
        dialog = ErrorDialog(e, sentry_event_id="sentry_test_id")

    qtbot.addWidget(dialog)
    dialog.show()

    # Check that toggling the debug info button does not throw an exception.
    dialog.debug_info_button.click()
    dialog.debug_info_button.click()

    # Check that the Yes button opens a link (to the AnkiHub forum).
    open_link_mock = mocker.patch("aqt.utils.openLink")
    dialog.button_box.button(QDialogButtonBox.StandardButton.Yes).click()
    open_link_mock.assert_called_once()

    # Check that clicking the No button does not throw an exception.
    dialog.button_box.button(QDialogButtonBox.StandardButton.No).click()


class TestFeatureFlags:
    @pytest.fixture(autouse=True)
    def setup(self):
        _feature_flags_update_callbacks.clear()

    def test_update_feature_flags_in_background(self, mocker: MockerFixture, qtbot: QtBot):
        MockAnkiHubClient = mocker.patch("ankihub.feature_flags.AnkiHubClient")
        mock_logger = mocker.patch("ankihub.feature_flags.LOGGER")
        mock_config = mocker.patch("ankihub.feature_flags.config")

        mock_anki_hub_client = MockAnkiHubClient.return_value
        feature_flags_dict = {"flag1": True, "flag2": False}
        mock_anki_hub_client.get_feature_flags.return_value = feature_flags_dict

        update_feature_flags_in_background()

        mock_logger_expected_calls = [
            call.info("Feature flags", feature_flags=feature_flags_dict),
            call.info("Set up feature flags."),
        ]
        qtbot.wait_until(lambda: len(mock_logger.info.mock_calls) == 2)

        mock_logger.assert_has_calls(mock_logger_expected_calls, any_order=True)
        mock_config.set_feature_flags.assert_called_with(feature_flags_dict)

    def test_add_feature_flags_update_callback(self):
        callback = MagicMock()
        add_feature_flags_update_callback(callback)
        assert callback in _feature_flags_update_callbacks


class TestRetainNidsWithAHNoteType:
    def test_retain_one_ah_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info = import_ah_note()
            nids = [NoteId(note_info.anki_nid)]
            assert retain_nids_with_ah_note_type(nids) == nids

    def test_retain_one_new_note_with_ah_note_type(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_type = import_ah_note_type()
            note = add_anki_note(note_type)
            nids = [note.id]
            assert retain_nids_with_ah_note_type(nids) == nids

    def test_filters_out_note_with_non_ah_note_type(
        self,
        anki_session_with_addon_data: AnkiSession,
        add_anki_note: AddAnkiNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note = add_anki_note(aqt.mw.col.models.by_name("Basic"))
            nids = [note.id]
            assert len(retain_nids_with_ah_note_type(nids)) == 0

    def test_combined(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info = import_ah_note()
            nid_1 = NoteId(note_info.anki_nid)

            note_type = import_ah_note_type()
            note = add_anki_note(note_type)
            nid_2 = note.id

            note = add_anki_note(aqt.mw.col.models.by_name("Basic"))
            nid_3 = note.id

            nids = [nid_1, nid_2, nid_3]
            assert retain_nids_with_ah_note_type(nids) == [nid_1, nid_2]


class MockUIForCreateCollaborativeDeck(Protocol):
    def __call__(self, deck_name: str) -> None: ...


@pytest.fixture
def mock_ui_for_create_collaborative_deck(
    mocker: MockerFixture,
    mock_study_deck_dialog_with_cb: MockStudyDeckDialogWithCB,
) -> MockUIForCreateCollaborativeDeck:
    """Mock the UI interaction for creating a collaborative deck.
    The deck_name determines which deck will be chosen for the upload."""

    def mock_ui_interaction_inner(deck_name) -> None:
        mock_study_deck_dialog_with_cb("ankihub.gui.operations.deck_creation.StudyDeck", deck_name)
        mocker.patch("ankihub.gui.operations.deck_creation.ask_user", return_value=True)
        mocker.patch("ankihub.gui.operations.deck_creation.showInfo")
        mocker.patch.object(DeckCreationConfirmationDialog, "run", return_value=True)

    return mock_ui_interaction_inner


class TestCreateCollaborativeDeck:
    @pytest.mark.qt_no_exception_capture
    @pytest.mark.parametrize(
        "creating_deck_fails",
        [True, False],
    )
    def test_basic(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        qtbot: QtBot,
        mock_ui_for_create_collaborative_deck: MockUIForCreateCollaborativeDeck,
        creating_deck_fails: bool,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            # Setup Anki deck with a note.
            deck_name = "test"
            anki_did = create_anki_deck(deck_name=deck_name)
            add_basic_anki_note_to_deck(anki_did)

            mock_ui_for_create_collaborative_deck(deck_name)

            mocker.patch.object(AnkiHubClient, "get_owned_decks", return_value=[])

            ah_did = next_deterministic_uuid()
            notes_data = [NoteInfoFactory.create()]
            create_ankihub_deck_mock = mocker.patch(
                "ankihub.gui.operations.deck_creation.create_ankihub_deck",
                return_value=DeckCreationResult(
                    ankihub_did=ah_did,
                    notes_data=notes_data,
                ),
                side_effect=Exception("test") if creating_deck_fails else None,
            )

            get_media_names_from_notes_data_mock = mocker.patch(
                "ankihub.gui.operations.deck_creation.get_media_names_from_notes_data",
                return_value=[],
            )
            start_media_upload_mock = mocker.patch.object(media_sync, "start_media_upload")
            showInfo_mock = mocker.patch("ankihub.gui.operations.deck_creation.showInfo")

            # Create the AnkiHub deck.
            if creating_deck_fails:
                create_collaborative_deck()
                qtbot.wait(500)
                showInfo_mock.assert_not_called()
            else:
                create_collaborative_deck()

                qtbot.wait_until(lambda: showInfo_mock.called)

                # Assert that the correct functions were called.
                create_ankihub_deck_mock.assert_called_once_with(deck_name, private=False, add_subdeck_tags=False)

                get_media_names_from_notes_data_mock.assert_called_once_with(notes_data)
                start_media_upload_mock.assert_called_once()

    def test_with_deck_name_existing(
        self,
        anki_session_with_addon_data: AnkiSession,
        mocker: MockerFixture,
        mock_ui_for_create_collaborative_deck: MockUIForCreateCollaborativeDeck,
    ):
        """When the user already has a deck with the same name, the deck creation is cancelled and
        a message is shown to the user."""
        with anki_session_with_addon_data.profile_loaded():
            # Setup Anki deck with a note.
            deck_name = "test"
            anki_did = create_anki_deck(deck_name=deck_name)
            add_basic_anki_note_to_deck(anki_did)

            mock_ui_for_create_collaborative_deck(deck_name)

            mocker.patch.object(
                AnkiHubClient,
                "get_owned_decks",
                return_value=[
                    DeckFactory(
                        name=deck_name,
                    )
                ],
            )

            showInfo_mock = mocker.patch("ankihub.gui.operations.deck_creation.showInfo")
            create_ankihub_deck_mock = mocker.patch("ankihub.gui.operations.deck_creation.create_ankihub_deck")

            create_collaborative_deck()

            showInfo_mock.assert_called_once()
            create_ankihub_deck_mock.assert_not_called()


class TestGetReviewCountForAHDeckSince:
    @pytest.mark.parametrize(
        "review_deltas, since_time, expected_count",
        [
            # No reviews since the specified date
            ([timedelta(days=-2), timedelta(days=-3)], timedelta(days=-1), 0),
            # Only reviews after the `since` date
            ([timedelta(seconds=1), timedelta(seconds=2)], timedelta(seconds=0), 2),
            # Boundary test with `since` date
            ([timedelta(seconds=0)], timedelta(seconds=0), 0),
            ([timedelta(seconds=0)], timedelta(seconds=-1), 1),
            # Reviews before and after the `since` date
            ([timedelta(seconds=-1), timedelta(seconds=1)], timedelta(seconds=0), 1),
        ],
    )
    def test_review_times_relative_to_since_time(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        review_deltas: List[timedelta],
        since_time: timedelta,
        expected_count: int,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)

            now = datetime.now()
            for review_delta in review_deltas:
                record_review_for_anki_nid(NoteId(note_info.anki_nid), now + review_delta)

            assert _get_review_count_for_ah_deck_since(ah_did=ah_did, since=now + since_time) == expected_count

    def test_with_multiple_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info_1 = import_ah_note(ah_did=ah_did)
            note_info_2 = import_ah_note(ah_did=ah_did)

            now = datetime.now()
            record_review_for_anki_nid(NoteId(note_info_1.anki_nid), now)
            record_review_for_anki_nid(NoteId(note_info_2.anki_nid), now + timedelta(seconds=1))

            since_time = now - timedelta(days=1)
            assert _get_review_count_for_ah_deck_since(ah_did=ah_did, since=since_time) == 2

    def test_with_review_for_other_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_info_1 = import_ah_note(ah_did=ah_did_1)

            ah_did_2 = install_ah_deck()
            note_info_2 = import_ah_note(ah_did=ah_did_2)

            now = datetime.now()
            record_review_for_anki_nid(NoteId(note_info_1.anki_nid), now)
            record_review_for_anki_nid(NoteId(note_info_2.anki_nid), now + timedelta(seconds=1))

            # Only the review for the first deck should be counted.
            since_time = now - timedelta(days=1)
            assert _get_review_count_for_ah_deck_since(ah_did=ah_did_1, since=since_time) == 1


class TestGetLastReviewTimeForAHDeck:
    @pytest.mark.parametrize(
        "review_deltas, expected_first_review_delta, expected_last_review_delta",
        [
            # Reviews in the past, the first and last review times are returned
            (
                [timedelta(days=-3), timedelta(days=-2), timedelta(days=-1)],
                timedelta(days=-3),
                timedelta(days=-1),
            ),
            # No reviews, None is returned
            ([], None, None),
        ],
    )
    def test_review_times_relative_to_since_time(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        review_deltas: List[timedelta],
        expected_first_review_delta: Optional[timedelta],
        expected_last_review_delta: Optional[timedelta],
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)

            now = datetime.now()
            for review_delta in review_deltas:
                record_review_for_anki_nid(NoteId(note_info.anki_nid), now + review_delta)

            first_and_last_time = _get_first_and_last_review_datetime_for_ah_deck(ah_did=ah_did)

            if expected_last_review_delta is not None:
                first_review_time, last_review_time = first_and_last_time

                expected_first_review_time = now + expected_first_review_delta
                assert_datetime_equal_ignore_milliseconds(
                    first_review_time,
                    expected_first_review_time,
                )

                expected_last_review_time = now + expected_last_review_delta
                assert_datetime_equal_ignore_milliseconds(last_review_time, expected_last_review_time)
            else:
                assert first_and_last_time is None

    def test_with_multiple_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info_1 = import_ah_note(ah_did=ah_did)
            note_info_2 = import_ah_note(ah_did=ah_did)

            expected_first_review_time = datetime.now()
            record_review_for_anki_nid(NoteId(note_info_1.anki_nid), expected_first_review_time)

            expected_last_review_time = expected_first_review_time + timedelta(days=1)
            record_review_for_anki_nid(NoteId(note_info_2.anki_nid), expected_last_review_time)

            (
                first_review_time,
                last_review_time,
            ) = _get_first_and_last_review_datetime_for_ah_deck(ah_did=ah_did)

            assert_datetime_equal_ignore_milliseconds(
                first_review_time,
                expected_first_review_time,
            )
            assert_datetime_equal_ignore_milliseconds(last_review_time, expected_last_review_time)

    def test_with_review_for_other_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_info_1 = import_ah_note(ah_did=ah_did_1)

            ah_did_2 = install_ah_deck()
            note_info_2 = import_ah_note(ah_did=ah_did_2)

            expected_review_time = datetime.now()
            record_review_for_anki_nid(NoteId(note_info_1.anki_nid), expected_review_time)
            record_review_for_anki_nid(
                NoteId(note_info_2.anki_nid),
                expected_review_time + timedelta(seconds=1),
            )

            # Only the review for the first deck should be considered.
            (
                first_review_time,
                last_review_time,
            ) = _get_first_and_last_review_datetime_for_ah_deck(ah_did=ah_did_1)

            assert first_review_time == last_review_time
            assert_datetime_equal_ignore_milliseconds(
                first_review_time,
                expected_review_time,
            )


class TestSendReviewData:
    def test_with_two_reviews_for_one_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info_1 = import_ah_note(ah_did=ah_did)
            note_info_2 = import_ah_note(ah_did=ah_did)

            first_review_time = datetime.now()
            record_review_for_anki_nid(NoteId(note_info_1.anki_nid), first_review_time)

            second_review_time = first_review_time + timedelta(days=1)
            record_review_for_anki_nid(NoteId(note_info_2.anki_nid), second_review_time)

            send_card_review_data_mock = mocker.patch.object(AnkiHubClient, "send_card_review_data")

            send_review_data()

            # Assert that the correct data was passed to the client method.
            send_card_review_data_mock.assert_called_once()

            card_review_data: CardReviewData = send_card_review_data_mock.call_args[0][0][0]
            assert card_review_data.ah_did == ah_did
            assert card_review_data.total_card_reviews_last_7_days == 2
            assert card_review_data.total_card_reviews_last_30_days == 2
            assert_datetime_equal_ignore_milliseconds(card_review_data.first_card_review_at, first_review_time)
            assert_datetime_equal_ignore_milliseconds(card_review_data.last_card_review_at, second_review_time)

    def test_without_reviews(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        mocker: MockerFixture,
    ) -> None:
        with anki_session_with_addon_data.profile_loaded():
            # We install the deck so that we get coverage for the case where a deck
            # has no reviews.
            install_ah_deck()

            send_card_review_data_mock = mocker.patch.object(AnkiHubClient, "send_card_review_data")

            send_review_data()

            # Assert that the correct data was passed to the client method.
            send_card_review_data_mock.assert_called_once()

            review_data_list = send_card_review_data_mock.call_args[0][0]
            assert review_data_list == []


def test_clear_empty_cards(anki_session_with_addon_data: AnkiSession, qtbot: QtBot):
    with anki_session_with_addon_data.profile_loaded():
        # Create a note with two cards.
        note = aqt.mw.col.new_note(
            aqt.mw.col.models.by_name("Cloze"),
        )
        note["Text"] = "{{c1::first}} {{c2::second}}"
        aqt.mw.col.add_note(note, DeckId(1))
        assert len(note.cards()) == 2  # sanity check

        # Cause the second card to be empty.
        note["Text"] = "{{c1::first}}"
        aqt.mw.col.update_note(note)
        assert len(note.cards()) == 2  # sanity check

        # Clear the empty card.
        clear_empty_cards()
        qtbot.wait_until(lambda: len(note.cards()) == 1)

        # Assert that the empty card was cleared.
        assert len(note.cards()) == 1


class TestChooseAnkiHubDeck:
    @pytest.mark.parametrize(
        "clicked_key, expected_chosen_deck_index",
        [(Qt.Key.Key_Enter, 0), (Qt.Key.Key_Escape, None)],
    )
    def test_choose_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        qtbot: QtBot,
        clicked_key: Qt.Key,
        expected_chosen_deck_index: Optional[int],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_dids = []
            ah_dids.append(install_ah_deck(ah_deck_name="Deck 1"))
            ah_dids.append(install_ah_deck(ah_deck_name="Deck 2"))

            # choose_ankihub_deck is blocking, so we setup a timer to press a key
            def on_timeout():
                qtbot.keyClick(qwidget.children()[0], clicked_key)

            QTimer.singleShot(0, on_timeout)

            qwidget = QWidget()
            result = choose_ankihub_deck(
                prompt="Choose a deck",
                ah_dids=list(ah_dids),
                parent=qwidget,
            )
            if expected_chosen_deck_index is None:
                assert result is None
            else:
                assert ah_dids.index(result) == expected_chosen_deck_index


class TestShowDialog:
    @pytest.mark.parametrize(
        "scrollable",
        [True, False],
    )
    def test_scrollable_argument(self, qtbot: QtBot, scrollable: bool):
        # This just tests that the function does not throw an exception for
        # different values of the scrollable argument.
        dialog = QDialog()
        qtbot.addWidget(dialog)
        show_dialog(text="some text", title="some title", parent=dialog, scrollable=scrollable)

    @pytest.mark.parametrize(
        "default_button_idx",
        [0, 1],
    )
    @pytest.mark.parametrize(
        "buttons",
        [
            ["Yes", "No"],
            [
                ("Yes", QDialogButtonBox.ButtonRole.AcceptRole),
                ("No", QDialogButtonBox.ButtonRole.RejectRole),
            ],
        ],
    )
    def test_button_callback(self, qtbot: QtBot, buttons, default_button_idx: int):
        button_index_from_cb: Optional[int] = None

        def callback(button_index: int):
            nonlocal button_index_from_cb
            button_index_from_cb = button_index

        dialog = QDialog()
        qtbot.addWidget(dialog)
        show_dialog(
            text="some text",
            title="some title",
            parent=dialog,
            callback=callback,
            buttons=buttons,
            default_button_idx=default_button_idx,
        )
        qtbot.keyClick(dialog, Qt.Key.Key_Enter)

        assert button_index_from_cb == default_button_idx


class TestPrivateConfigMigrations:
    def test_oprphaned_deck_extensions_are_removed(self, next_deterministic_uuid: Callable[[], uuid.UUID]):
        # Add a deck extension without a corressponding deck to the private config.
        ah_did = next_deterministic_uuid()
        deck_extension = DeckExtensionFactory.create(ah_did=ah_did)
        config.create_or_update_deck_extension_config(deck_extension)

        # sanity check
        assert config.deck_extensions_ids_for_ah_did(ah_did) == [deck_extension.id]

        # Reload the private config to trigger the migration.
        config.setup_private_config()

        assert config.deck_extensions_ids_for_ah_did(ah_did) == []

    def test_block_exam_subdeck_configs_migration(
        self,
        anki_session_with_addon_data: AnkiSession,
    ):
        """Test that migration removes invalid configs while preserving valid ones.

        Tests that migration correctly:
        - Preserves valid configs added via proper API
        - Removes configs with invalid date formats (e.g., "invalid-date", "2025/01/01")
        - Removes configs for non-existent Anki subdecks
        """
        with anki_session_with_addon_data.profile_loaded():
            # Create two valid subdecks
            root_deck_name = "Test Deck"
            subdeck1_name = f"{root_deck_name}::Valid Subdeck 1"
            subdeck1_id = create_anki_deck(subdeck1_name)
            subdeck2_name = f"{root_deck_name}::Valid Subdeck 2"
            subdeck2_id = create_anki_deck(subdeck2_name)

            # Add valid configs using the proper API
            valid_due_date1 = "2025-12-31"
            valid_due_date2 = "2026-01-15"
            config.upsert_block_exam_subdeck(
                DeckId(subdeck1_id),
                due_date=valid_due_date1,
                origin_hint=BlockExamSubdeckOrigin.SMART_SEARCH,
            )
            config.upsert_block_exam_subdeck(
                DeckId(subdeck2_id),
                due_date=valid_due_date2,
                origin_hint=BlockExamSubdeckOrigin.SMART_SEARCH,
            )

            # Verify valid configs were added
            assert len(config.get_block_exam_subdecks()) == 2

            # Use a non-existent subdeck ID for invalid config testing
            fake_subdeck_id = 999999

            # Inject multiple invalid configs into the config file
            self._inject_subdeck_configs_to_file(
                {"subdeck_id": subdeck1_id, "due_date": "invalid-date"},  # Invalid format
                {"subdeck_id": subdeck2_id, "due_date": "2025/01/01"},  # Wrong format (slashes)
                {"subdeck_id": fake_subdeck_id, "due_date": "2025-12-31"},  # Non-existent subdeck
            )

            # Trigger migration by reloading config
            config.setup_private_config()

            # Verify migration results: valid configs preserved, invalid configs removed
            configs = config.get_block_exam_subdecks()
            assert len(configs) == 2

            # Verify the preserved configs have correct data
            config_dict = {cfg.subdeck_id: cfg.due_date for cfg in configs}
            assert config_dict[subdeck1_id] == valid_due_date1
            assert config_dict[subdeck2_id] == valid_due_date2

    def _inject_subdeck_configs_to_file(self, *subdeck_configs):
        """Helper to inject subdeck configs directly into the private config file.

        This simulates corrupted/invalid data in the config file that should be
        cleaned up by migration when the config is reloaded.

        Args:
            *subdeck_configs: One or more dictionaries representing subdeck configs
        """
        private_config_dict = config._private_config.to_dict()
        private_config_dict["block_exams_subdecks"].extend(subdeck_configs)

        # Write the modified dict to the file
        with open(config._private_config_path, "w") as f:
            f.write(json.dumps(private_config_dict, indent=4, sort_keys=True))


class TestOptionalTagSuggestionDialog:
    def test_submit_tags_for_validated_groups(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)

            deck_extensions = [
                DeckExtensionFactory.create(
                    ah_did=ah_did,
                    tag_group_name="tag_group_1",
                ),
                DeckExtensionFactory.create(
                    ah_did=ah_did,
                    tag_group_name="tag_group_2",
                ),
                DeckExtensionFactory.create(
                    ah_did=ah_did,
                    tag_group_name="tag_group_3",
                ),
            ]

            # Add the deck extensions to the config
            for deck_extension in deck_extensions:
                config.create_or_update_deck_extension_config(deck_extension)

            # Mock client methods
            index_of_invalid_tag_group = 0
            validation_responses = []
            for i, deck_extension in enumerate(deck_extensions):
                validation_reponse = TagGroupValidationResponse(
                    tag_group_name=deck_extension.tag_group_name,
                    success=i != index_of_invalid_tag_group,
                    errors=[],
                    deck_extension_id=deck_extension.id,
                )
                validation_responses.append(validation_reponse)

            get_deck_extensions_mock = mocker.patch(
                "ankihub.gui.optional_tag_suggestion_dialog.AnkiHubClient.get_deck_extensions",
                return_value=deck_extensions,
            )

            prevalidate_tag_groups_mock = mocker.patch(
                "ankihub.main.optional_tag_suggestions.AnkiHubClient.prevalidate_tag_groups",
                return_value=validation_responses,
            )

            widget = QWidget()
            qtbot.addWidget(widget)
            dialog = OptionalTagsSuggestionDialog(parent=widget, nids=[NoteId(note_info.anki_nid)])

            # Mock the suggest_tags_for_groups method which is called when the submit button is clicked
            suggest_tags_for_groups_mock = mocker.patch.object(dialog._optional_tags_helper, "suggest_tags_for_groups")

            dialog.show()

            qtbot.mouseClick(dialog.submit_btn, Qt.MouseButton.LeftButton)

            qtbot.wait_until(lambda: suggest_tags_for_groups_mock.called)

            get_deck_extensions_mock.assert_called_once()
            prevalidate_tag_groups_mock.assert_called_once()

            # Assert that suggest_tags_for_groups is called for the groups which were validated successfully
            kwargs = suggest_tags_for_groups_mock.call_args.kwargs
            assert set(kwargs["tag_groups"]) == set(
                [deck_extensions[1].tag_group_name, deck_extensions[2].tag_group_name]
            )
            assert not kwargs["auto_accept"]

    @pytest.mark.parametrize(
        "user_relation, expected_checkbox_is_visible",
        [
            (UserDeckExtensionRelation.OWNER, True),
            (UserDeckExtensionRelation.MAINTAINER, True),
            (UserDeckExtensionRelation.SUBSCRIBER, False),
        ],
    )
    def test_submit_without_review_checkbox_hidden_when_user_cant_use_it(
        self,
        anki_session_with_addon_data: AnkiSession,
        qtbot: QtBot,
        install_ah_deck: InstallAHDeck,
        import_ah_note: ImportAHNote,
        mocker: MockerFixture,
        user_relation: UserDeckExtensionRelation,
        expected_checkbox_is_visible: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = install_ah_deck()
            note_info = import_ah_note(ah_did=ah_did)

            deck_extension = DeckExtensionFactory.create(
                ah_did=ah_did,
                tag_group_name="tag_group_1",
                user_relation=user_relation,
            )
            config.create_or_update_deck_extension_config(deck_extension)

            validation_reponse = TagGroupValidationResponse(
                tag_group_name=deck_extension.tag_group_name,
                success=True,
                errors=[],
                deck_extension_id=deck_extension.id,
            )

            mocker.patch(
                "ankihub.gui.optional_tag_suggestion_dialog.AnkiHubClient.get_deck_extensions",
                return_value=[deck_extension],
            )

            mocker.patch(
                "ankihub.main.optional_tag_suggestions.AnkiHubClient.prevalidate_tag_groups",
                return_value=[validation_reponse],
            )

            widget = QWidget()
            qtbot.addWidget(widget)
            dialog = OptionalTagsSuggestionDialog(parent=widget, nids=[NoteId(note_info.anki_nid)])

            dialog.show()
            qtbot.wait(500)

            assert dialog.auto_accept_cb.isVisible() == expected_checkbox_is_visible


class TestUtils:
    def test_extract_argument_when_argument_not_found(self):
        def func(*args, **kwargs):
            return

        args = [1, 2, 3]
        kwargs = {"a": True, "b": False}

        with pytest.raises(ValueError):
            args, kwargs, value = extract_argument(
                func,
                args=args,
                kwargs=kwargs,
                arg_name="after_sync",
            )

    def test_extract_argument_with_keyword_arguments(self):
        def func(*, a, b):
            return

        kwargs = {"a": True, "b": "test"}

        args, kwargs, value = extract_argument(
            func,
            args=tuple(),
            kwargs=kwargs,
            arg_name="b",
        )

        assert not args
        assert kwargs == {"a": True}
        assert value == "test"


@pytest.mark.parametrize(
    "show_cancel_button, text_of_button_to_click, expected_return_value",
    [
        # Without cancel button
        (False, "Yes", True),
        (False, "No", False),
        # With cancel button
        (True, "Yes", True),
        (True, "No", False),
        (True, "Cancel", None),
    ],
)
def test_ask_user(
    qtbot: QtBot,
    show_cancel_button: bool,
    text_of_button_to_click: str,
    expected_return_value: bool,
    latest_instance_tracker: LatestInstanceTracker,
):
    latest_instance_tracker.track(_Dialog)

    # Click a button on the dialog after it is shown
    def click_button():
        dialog = latest_instance_tracker.get_latest_instance(_Dialog)
        button = next(button for button in dialog.button_box.buttons() if text_of_button_to_click in button.text())
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    QTimer.singleShot(0, click_button)

    # Show the dialog (blocks until the button is clicked)
    return_value = ask_user(
        text="Do you want to continue?",
        title="Continue?",
        show_cancel_button=show_cancel_button,
    )
    assert return_value == expected_return_value


class TestAnkiHubDBMigrations:
    def test_migrate_from_schema_version_1(
        self, next_deterministic_uuid, next_deterministic_id, ankihub_db: _AnkiHubDB
    ):
        """Test the migration from schema version 1 to the newest schema.

        This test creates a temporary database with an old schema (version 1),
        adds a single row to the notes table (which was the only table at the time),
        and then applies the migrations to upgrade the database to the newest schema.

        After the migration, it checks that:
        - The row in the notes table was migrated correctly.
        - The table and index definitions in the migrated database are the same
          as those in the original database.

        Limitations:
        - The test is not exhaustive as it only starts with one row in one table.
        - It does not test the migration of data in other tables and fields that
            were added in newer schema versions.
        """
        with tempfile.TemporaryDirectory() as f:
            # Create a database with an old schema (version 1) in a temporary directory
            migration_test_db_path = Path(f) / "test.db"
            conn = sqlite3.Connection(migration_test_db_path)

            inital_table_definition = """
                CREATE TABLE notes (
                    ankihub_note_id STRING PRIMARY KEY,
                    ankihub_deck_id STRING,
                    anki_note_id INTEGER,
                    anki_note_type_id INTEGER,
                    mod INTEGER
                )
            """
            conn.execute(inital_table_definition)
            conn.commit()

            # Add a row to the notes table
            ah_nid = next_deterministic_uuid()
            ah_did = next_deterministic_uuid()
            anki_nid = next_deterministic_id()
            anki_mid = next_deterministic_id()
            mod = 7
            conn.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, ?)",
                (str(ah_nid), str(ah_did), anki_nid, anki_mid, mod),
            )
            conn.commit()

            # Set the user version to 1
            conn.execute("PRAGMA user_version = 1")
            conn.commit()

            conn.close()

            # Apply the migrations
            migration_test_db = _AnkiHubDB()
            migration_test_db.setup_and_migrate(db_path=migration_test_db_path)

            # Assert that the row was migrated correctly
            note = AnkiHubNote.get()
            assert note.ankihub_note_id == ah_nid
            assert note.ankihub_deck_id == ah_did
            assert note.anki_note_id == anki_nid
            assert note.anki_note_type_id == anki_mid
            assert note.mod == mod

            # Get the expected table and index definitions
            table_definitions_sql = "SELECT sql FROM sqlite_master WHERE type='table'"
            index_definitions_sql = "SELECT sql FROM sqlite_master WHERE type='index'"

            conn = sqlite3.Connection(ankihub_db.database_path)
            expected_table_definitions = conn.execute(table_definitions_sql).fetchall()
            expected_index_definitions = conn.execute(index_definitions_sql).fetchall()
            conn.close()

            # Get the table and index definitions after the migration for the migration test db
            conn = sqlite3.Connection(migration_test_db_path)
            table_definitions = conn.execute(table_definitions_sql).fetchall()
            index_definitions = conn.execute(index_definitions_sql).fetchall()
            conn.close()

            # Assert that the table and index definitions are the same for the two databases
            assert set(table_definitions) == set(expected_table_definitions)
            assert set(index_definitions) == set(expected_index_definitions)
            assert ankihub_db.database_path != migration_test_db_path  # sanity check


class TestDatadogLogHandler:
    @pytest.mark.parametrize("send_logs_to_datadog_feature_flag", [True, False])
    def test_emit_and_flush(self, mocker: MockerFixture, send_logs_to_datadog_feature_flag: bool):
        feature_flags = config.get_feature_flags()
        feature_flags["send_addon_logs_to_datadog"] = send_logs_to_datadog_feature_flag

        # Mock the requests.post call to always return a response with status code 202
        response = Mock()
        response.status_code = 202
        post_mock = mocker.patch("requests.post", return_value=response)

        # Create a DatadogLogHandler and a LogRecord
        handler = DatadogLogHandler()
        record = LogRecord(
            name="test",
            level=0,
            pathname="",
            lineno=0,
            msg='{"event": "test"}',
            args=(),
            exc_info=None,
        )

        # Call emit and flush on the handler and assert the behavior
        if send_logs_to_datadog_feature_flag:
            handler.flush()

            handler.emit(record)
            assert len(handler.buffer) == 1
            assert handler.buffer[0] == record

            handler.flush()
            assert len(handler.buffer) == 0

            post_mock.assert_called_once()
            assert post_mock.call_args[0] == ("https://http-intake.logs.datadoghq.com/api/v2/logs",)
        else:
            handler.emit(record)
            assert len(handler.buffer) == 1
            handler.flush(record)
            assert len(handler.buffer) == 0
            post_mock.assert_not_called()

    def test_periodic_flush(self, mocker):
        feature_flags = config.get_feature_flags()
        feature_flags["send_addon_logs_to_datadog"] = True

        # Mock the requests.post call to always return a response with status code 202
        response = Mock()
        response.status_code = 202
        post_mock = mocker.patch("requests.post", return_value=response)

        # Create a DatadogLogHandler with a short flush interval and a LogRecord
        handler = DatadogLogHandler(send_interval=0.01)
        record = LogRecord(
            name="test",
            level=0,
            pathname="",
            lineno=0,
            msg='{"event": "test"}',
            args=(),
            exc_info=None,
        )

        # Call emit on the handler and wait for the periodic flush to happen
        handler.emit(record)
        time.sleep(0.3)

        # Check that the buffer is empty and that requests.post was called once
        assert len(handler.buffer) == 0
        post_mock.assert_called_once()
        assert post_mock.call_args[0] == ("https://http-intake.logs.datadoghq.com/api/v2/logs",)

    def test_capacity_flush(self, mocker):
        feature_flags = config.get_feature_flags()
        feature_flags["send_addon_logs_to_datadog"] = True

        # Create a DatadogLogHandler with a short flush interval and a LogRecord
        handler = DatadogLogHandler(capacity=3)
        record = LogRecord(
            name="test",
            level=0,
            pathname="",
            lineno=0,
            msg='{"event": "test"}',
            args=(),
            exc_info=None,
        )

        flush_mock = mocker.patch.object(handler, "flush")

        # Call emit on the handler
        handler.emit(record)
        handler.emit(record)
        handler.emit(record)

        # Check that the buffer is full and that flush was called
        assert len(handler.buffer) == 3
        flush_mock.assert_called_once()

    @pytest.mark.parametrize("addon_version", ["dev", "test_version"])
    def test_handler_setup(self, anki_session_with_addon_data: AnkiSession, addon_version: str):
        from ankihub import settings

        settings.ADDON_VERSION = addon_version
        entry_point.run()
        with anki_session_with_addon_data.profile_loaded():
            std_logger = logging.getLogger("ankihub")
            datadog_log_handler = next(
                (handler for handler in std_logger.handlers if isinstance(handler, DatadogLogHandler)),
                None,
            )
            if addon_version == "dev":
                assert datadog_log_handler is None
            else:
                assert datadog_log_handler


class TestNoteTypeWithUpdatedTemplates:
    @pytest.mark.parametrize("use_new_templates", [True, False])
    def test_basic(self, use_new_templates: bool):
        old_note_type_content = "old content"
        old_note_type = {
            "tmpls": [{"qfmt": old_note_type_content, "afmt": old_note_type_content}],
            "css": old_note_type_content,
        }

        new_note_type_content = "new content"
        new_note_type = {
            "tmpls": [{"qfmt": new_note_type_content, "afmt": new_note_type_content}],
            "css": new_note_type_content,
        }

        updated_note_type = note_type_with_updated_templates_and_css(
            old_note_type=old_note_type,
            new_note_type=new_note_type if use_new_templates else None,
        )
        assert len(updated_note_type["tmpls"]) == 1
        template = updated_note_type["tmpls"][0]

        verify(
            template["qfmt"],
            options=NamerFactory.with_parameters("qfmt", use_new_templates),
        )
        verify(
            template["afmt"],
            options=NamerFactory.with_parameters("afmt", use_new_templates),
        )
        verify(
            updated_note_type["css"],
            options=NamerFactory.with_parameters("css", use_new_templates),
        )

    @pytest.mark.parametrize("use_new_templates", [True, False])
    def test_with_migrating_content_from_old_note_type(self, use_new_templates: bool):
        content_to_migrate = "content to migrate"
        old_note_type_html_content = f"old content\n{ANKIHUB_HTML_END_COMMENT}\n{content_to_migrate}"
        old_note_type_css_content = f"old css\n{ANKIHUB_CSS_END_COMMENT}\n{content_to_migrate}"
        old_note_type = {
            "tmpls": [{"qfmt": old_note_type_html_content, "afmt": old_note_type_html_content}],
            "css": old_note_type_css_content,
        }

        new_note_type_content = "new content"
        new_note_type = {
            "tmpls": [{"qfmt": new_note_type_content, "afmt": new_note_type_content}],
            "css": new_note_type_content,
        }

        updated_note_type = note_type_with_updated_templates_and_css(
            old_note_type=old_note_type,
            new_note_type=new_note_type if use_new_templates else None,
        )
        assert len(updated_note_type["tmpls"]) == 1
        template = updated_note_type["tmpls"][0]

        verify(
            template["qfmt"],
            options=NamerFactory.with_parameters("qfmt", use_new_templates),
        )
        verify(
            template["afmt"],
            options=NamerFactory.with_parameters("afmt", use_new_templates),
        )
        verify(
            updated_note_type["css"],
            options=NamerFactory.with_parameters("css", use_new_templates),
        )

    @pytest.mark.parametrize("use_new_templates", [True, False])
    def test_with_added_template(self, use_new_templates: bool):
        old_template_content = "old content"
        old_note_type = {
            "tmpls": [{"qfmt": old_template_content, "afmt": old_template_content}],
            "css": "",
        }

        new_template1_content = "new content 1"
        new_template2_content = "new content 2"
        new_note_type = {
            "tmpls": [
                {"qfmt": new_template1_content, "afmt": new_template1_content},
                {"qfmt": new_template2_content, "afmt": new_template2_content},
            ],
            "css": "",
        }

        updated_note_type = note_type_with_updated_templates_and_css(
            old_note_type=old_note_type,
            new_note_type=new_note_type if use_new_templates else None,
        )
        if use_new_templates:
            assert len(updated_note_type["tmpls"]) == 2
            assert new_template1_content in updated_note_type["tmpls"][0]["qfmt"]
            assert new_template2_content in updated_note_type["tmpls"][1]["qfmt"]
        else:
            assert len(updated_note_type["tmpls"]) == 1
            assert old_template_content in updated_note_type["tmpls"][0]["qfmt"]

    @pytest.mark.parametrize("use_new_templates", [True, False])
    def test_with_removed_template(self, use_new_templates: bool):
        old_template1_content = "old content 1"
        old_template2_content = "old content 2"
        old_note_type = {
            "tmpls": [
                {"qfmt": old_template1_content, "afmt": old_template1_content},
                {"qfmt": old_template2_content, "afmt": old_template2_content},
            ],
            "css": "",
        }

        new_template_content = "new content"
        new_note_type = {
            "tmpls": [
                {"qfmt": new_template_content, "afmt": new_template_content},
            ],
            "css": "",
        }

        updated_note_type = note_type_with_updated_templates_and_css(
            old_note_type=old_note_type,
            new_note_type=new_note_type if use_new_templates else None,
        )
        if use_new_templates:
            assert len(updated_note_type["tmpls"]) == 1
            assert new_template_content in updated_note_type["tmpls"][0]["qfmt"]
        else:
            assert len(updated_note_type["tmpls"]) == 2
            assert old_template1_content in updated_note_type["tmpls"][0]["qfmt"]
            assert old_template2_content in updated_note_type["tmpls"][1]["qfmt"]


def test_get_daily_review_data_since_last_sync(mocker, anki_session_with_addon_data):
    with anki_session_with_addon_data.profile_loaded():
        last_sync = datetime.now() - timedelta(days=2)
        yesterday = datetime.now() - timedelta(days=1)
        mock_rows = [
            (int(datetime.timestamp(last_sync) * 1000) + 1000, 1, 30),
            (int(datetime.timestamp(last_sync) * 1000) + 2000, 2, 40),
            (int(datetime.timestamp(yesterday) * 1000) - 1000, 3, 50),
            (int(datetime.timestamp(yesterday) * 1000) - 500, 4, 60),
        ]

        mocker.patch("ankihub.main.review_data.aqt.mw.col.db.all", return_value=mock_rows)

        result = get_daily_review_summaries_since_last_sync(last_sync)

        expected_data = [
            DailyCardReviewSummary(
                total_cards_studied=2,
                total_time_reviewing=70,
                total_cards_marked_as_again=1,
                total_cards_marked_as_hard=1,
                total_cards_marked_as_good=0,
                total_cards_marked_as_easy=0,
                review_session_date=(last_sync).date(),
            ),
            DailyCardReviewSummary(
                total_cards_studied=2,
                total_time_reviewing=110,
                total_cards_marked_as_again=0,
                total_cards_marked_as_hard=0,
                total_cards_marked_as_good=1,
                total_cards_marked_as_easy=1,
                review_session_date=(yesterday).date(),
            ),
        ]

        assert len(result) == len(expected_data)
        for res, exp in zip(result, expected_data):
            assert res.total_cards_studied == exp.total_cards_studied
            assert res.total_time_reviewing == exp.total_time_reviewing
            assert res.total_cards_marked_as_again == exp.total_cards_marked_as_again
            assert res.total_cards_marked_as_hard == exp.total_cards_marked_as_hard
            assert res.total_cards_marked_as_good == exp.total_cards_marked_as_good
            assert res.total_cards_marked_as_easy == exp.total_cards_marked_as_easy
            assert res.review_session_date == exp.review_session_date


def test_get_daily_review_data_no_reviews(mocker, anki_session_with_addon_data):
    with anki_session_with_addon_data.profile_loaded():
        last_sync = datetime.now() - timedelta(days=2)
        mocker.patch("ankihub.main.review_data.aqt.mw.col.db.all", return_value=[])

        result = get_daily_review_summaries_since_last_sync(last_sync)

        assert result == []


def test_send_daily_review_summaries_with_data(mocker):
    last_summary_sent_date = (datetime.now() - timedelta(days=1)).date()
    mock_summary = MagicMock()

    MockAnkiHubClient = mocker.patch("ankihub.main.review_data.AnkiHubClient")
    mock_get_daily_review_summaries = mocker.patch(
        "ankihub.main.review_data.get_daily_review_summaries_since_last_sync"
    )

    mock_get_daily_review_summaries.return_value = [mock_summary]
    mock_anki_hub_client = MockAnkiHubClient.return_value

    send_daily_review_summaries(last_summary_sent_date)

    mock_get_daily_review_summaries.assert_called_once_with(last_summary_sent_date)
    mock_anki_hub_client.send_daily_card_review_summaries.assert_called_once_with([mock_summary])


def test_send_daily_review_summaries_without_data(mocker):
    last_summary_sent_date = (datetime.now() - timedelta(days=1)).date()

    MockAnkiHubClient = mocker.patch("ankihub.main.review_data.AnkiHubClient")
    mock_get_daily_review_summaries = mocker.patch(
        "ankihub.main.review_data.get_daily_review_summaries_since_last_sync"
    )

    mock_get_daily_review_summaries.return_value = []
    mock_anki_hub_client = MockAnkiHubClient.return_value

    send_daily_review_summaries(last_summary_sent_date)

    mock_get_daily_review_summaries.assert_called_once_with(last_summary_sent_date)
    mock_anki_hub_client.send_daily_card_review_summaries.assert_not_called()


def url_mh_integrations_preview(slug: str) -> str:
    # Test-specific replacement for settings.url_mh_integrations_preview using DEFAULT_APP_URL
    # We need this because:
    # 1. settings.config.app_url is not initialized during test startup
    # 2. pytest needs these URLs during test collection for parametrize data
    return f"{DEFAULT_APP_URL}/integrations/mcgraw-hill/preview/{slug}"


@pytest.mark.parametrize(
    "tag, expected_resource",
    [
        # With B&B tag
        (
            "#AK_Step1_v12::#B&B::03_Biochem::03_Amino_Acids::04_Ammonia",
            Resource(
                "Ammonia",
                url_mh_integrations_preview("step1-bb-3-3-4"),
                1,
            ),
        ),
        # With First Aid tag
        (
            "#AK_Step2_v12::#FirstAid::14_Pulm::16_Nose_and_Throat::01_Rhinitis",
            Resource(
                "Rhinitis",
                url_mh_integrations_preview("step2-fa-14-16-1"),
                2,
            ),
        ),
        # With lowercase tag
        (
            "#ak_step1_v12::#b&b::03_biochem::03_amino_acids::04_ammonia",
            Resource(
                "Ammonia",
                url_mh_integrations_preview("step1-bb-3-3-4"),
                1,
            ),
        ),
        # With trailing Extra tag part that should be ignored
        (
            "#AK_Step1_v12::#B&B::03_Biochem::03_Amino_Acids::04_Ammonia::Extra",
            Resource(
                "Ammonia",
                url_mh_integrations_preview("step1-bb-3-3-4"),
                1,
            ),
        ),
        # With tag part group starting with "*" that should be ignored
        (
            (
                "#AK_Step1_v12::#FirstAid::05_Pharm::02_Autonomic_Drugs::15_beta-blockers::"
                "*B-Antagonists::Cardioselective_B1_Antagonists"
            ),
            Resource(
                "Beta-blockers",
                url_mh_integrations_preview("step1-fa-5-2-15"),
                1,
            ),
        ),
        # With number later in tag part
        (
            "#AK_Step1_v12::#FirstAid::01_Biochem::03_Laboratory_Techniques::02_CRISPR/Cas9",
            Resource(
                "Crispr/cas9",
                url_mh_integrations_preview("step1-fa-1-3-2"),
                1,
            ),
        ),
        # With invalid tag
        ("invalid_tag", None),
        # With invalid tag (core tag part doesn't start with number)
        (
            "#AK_Step1_v12::#FirstAid::Biochem::03_Laboratory_Techniques::CRISPR/Cas9",
            None,
        ),
    ],
)
def test_mh_tag_to_resource_title_and_slug(tag: str, expected_resource: Optional[Resource]):
    assert mh_tag_to_resource(tag) == expected_resource


class TestAddNoteTypeFields:
    @pytest.mark.parametrize(
        "ah_field_names, anki_field_names, new_field_names, expected_field_names",
        [
            # Add a single field
            (
                ["Text", "ankihub_id"],
                ["Text", "new_field", "ankihub_id"],
                ["new_field"],
                ["Text", "new_field", "ankihub_id"],
            ),
            # ... Using the order from the Anki note type
            (
                ["Text", "ankihub_id"],
                ["new_field", "Text", "ankihub_id"],
                ["new_field"],
                ["new_field", "Text", "ankihub_id"],
            ),
            # ... ankihub_id should always be the last field
            (
                ["Text", "ankihub_id"],
                [
                    "ankihub_id",
                    "new_field",
                    "Text",
                ],
                ["new_field"],
                ["new_field", "Text", "ankihub_id"],
            ),
            # ... Ignore new fields not mentioned in the new_field_names
            (
                ["Text", "ankihub_id"],
                ["Text", "new_field", "ankihub_id", "extra_field"],
                ["new_field"],
                ["Text", "new_field", "ankihub_id"],
            ),
            # ... When the anki note type misses some fields, add them to the end (but before ankihub_id)
            (
                ["Text", "Extra", "ankihub_id"],
                ["Text", "new_field", "ankihub_id"],
                ["new_field"],
                ["Text", "new_field", "Extra", "ankihub_id"],
            ),
            # Add multiple fields
            (
                ["Text", "ankihub_id"],
                ["Text", "new_field1", "new_field2", "ankihub_id"],
                ["new_field1", "new_field2"],
                ["Text", "new_field1", "new_field2", "ankihub_id"],
            ),
        ],
    )
    def test_add_note_type_fields(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note_type: ImportAHNoteType,
        next_deterministic_uuid: Callable[[], uuid.UUID],
        mocker: MockerFixture,
        ankihub_db: _AnkiHubDB,
        ah_field_names: List[str],
        anki_field_names: List[str],
        new_field_names: List[str],
        expected_field_names: List[str],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()

            ah_note_type = note_type_with_field_names(ah_field_names)
            ah_note_type = import_ah_note_type(ah_did=ah_did, note_type=ah_note_type, force_new=True)

            anki_note_type = note_type_with_field_names(anki_field_names)
            anki_note_type["id"] = ah_note_type["id"]

            # Mock udpate_note_type client method to return the note type passed to it
            update_note_type_mock = mocker.patch.object(
                AnkiHubClient,
                "update_note_type",
                side_effect=lambda _, note_type, __: note_type,
            )

            add_note_type_fields(
                ah_did=ah_did,
                note_type=anki_note_type,
                new_field_names=new_field_names,
            )

            ah_db_note_type = ankihub_db.note_type_dict(ah_note_type["id"])

            # Assert field names are correct
            assert [field["name"] for field in ah_db_note_type["flds"]] == expected_field_names

            # Assert field ord values go from 0 to n-1
            assert [field["ord"] for field in ah_db_note_type["flds"]] == list(range(len(ah_db_note_type["flds"])))

            # Assert client method was called with the same note type as the one in the db
            note_type_passed_to_client = update_note_type_mock.call_args[0][1]
            assert note_type_passed_to_client == ah_db_note_type


class TestDeckImportSummaryDialog:
    """Test suite for deck import summary dialog scenarios."""

    # Set to x to wait x milliseconds after showing the dialog - useful to look at the dialog
    wait = None

    @pytest.fixture
    def mock_dependencies(self, mocker: MockerFixture) -> Dict[str, Any]:
        """Mock dependencies needed for the dialog."""
        # Mock functions
        mock_logged_into_ankiweb = mocker.patch(
            "ankihub.gui.operations.deck_installation.logged_into_ankiweb",
            return_value=True,
        )
        mock_show_dialog = mocker.spy(ankihub.gui.operations.deck_installation, "show_dialog")

        # Mock DeckManagementDialog
        mock_deck_management = Mock()
        mocker.patch("ankihub.gui.decks_dialog.DeckManagementDialog", mock_deck_management)

        return {
            "logged_into_ankiweb": mock_logged_into_ankiweb,
            "show_dialog": mock_show_dialog,
            "deck_management": mock_deck_management,
        }

    def get_dialog_message(self, mock_dependencies: Dict[str, Any]) -> str:
        """Extract the HTML message passed to show_dialog."""
        return mock_dependencies["show_dialog"].call_args[0][0]

    @pytest.mark.parametrize("logged_to_ankiweb", [True, False])
    def test_single_deck_created_separately(
        self,
        mock_dependencies: Dict[str, Any],
        logged_to_ankiweb: bool,
        qtbot: QtBot,
    ):
        """Test scenario: Single deck was created separately."""
        mock_dependencies["logged_into_ankiweb"].return_value = logged_to_ankiweb

        ankihub_deck_names = ["Cardiology Deck"]
        anki_deck_names = ankihub_deck_names.copy()

        import_result = AnkiHubImportResultFactory.create(merged_with_existing_deck=False)

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=[import_result],
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "The deck <b>Cardiology Deck</b> is ready to study." in message

        if logged_to_ankiweb:
            assert "Download from AnkiWeb" in message
        else:
            assert "Download from AnkiWeb" not in message

    def test_single_deck_merged_same_name(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Single deck was merged into existing deck with same name."""
        ankihub_deck_names = ["Internal Medicine"]
        anki_deck_names = ankihub_deck_names.copy()

        import_result = AnkiHubImportResultFactory.create(merged_with_existing_deck=True)

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=[import_result],
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "You already have the deck <b>Internal Medicine</b>!" in message
        assert "We've merged the new deck into the existing one." in message

    def test_single_deck_merged_different_name(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Single deck was merged into existing deck with different name."""
        ankihub_deck_names = ["MCAT Biology"]
        anki_deck_names = ["My Custom Biology Deck"]

        import_result = AnkiHubImportResultFactory.create(merged_with_existing_deck=True)

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=[import_result],
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "<b>MCAT Biology</b> was merged into <b>My Custom Biology Deck</b>" in message
        assert "due to overlapping content" in message

    def test_multiple_decks_all_created(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Multiple decks all created separately."""
        ankihub_deck_names = ["Deck name A", "Deck name B", "Deck name C"]
        anki_deck_names = ankihub_deck_names.copy()

        import_results = [AnkiHubImportResultFactory.create(merged_with_existing_deck=False) for _ in range(3)]

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=import_results,
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "The following decks are ready to study:" in message
        assert "<b>Deck name A</b>" in message
        assert "<b>Deck name B</b>" in message
        assert "<b>Deck name C</b>" in message

    def test_multiple_decks_all_merged_same_name(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Multiple decks all merged with same name."""
        ankihub_deck_names = ["Deck name A", "Deck name B", "Deck name C"]
        anki_deck_names = ankihub_deck_names.copy()

        import_results = [AnkiHubImportResultFactory.create(merged_with_existing_deck=True) for _ in range(3)]

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=import_results,
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "New decks were merged into existing decks with matching names:" in message
        assert "<b>Deck name A</b>" in message
        assert "<b>Deck name B</b>" in message
        assert "<b>Deck name C</b>" in message

    def test_multiple_decks_all_merged_different_name(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Multiple decks all merged with different name."""
        ankihub_deck_names = ["Deck name A", "Deck name B", "Deck name C"]
        anki_deck_names = [
            "Existing Deck Name A",
            "Existing Deck Name B",
            "Existing Deck Name C",
        ]

        import_results = [AnkiHubImportResultFactory.create(merged_with_existing_deck=True) for _ in range(3)]

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=import_results,
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "Some of the decks you subscribed to matched ones you already had." in message
        assert "We've merged them to avoid duplicates:" in message
        assert "<b>Deck name A</b> → <b>Existing Deck Name A</b>" in message
        assert "<b>Deck name B</b> → <b>Existing Deck Name B</b>" in message
        assert "<b>Deck name C</b> → <b>Existing Deck Name C</b>" in message

    def test_multiple_subscribed_decks_mixed(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Multiple subscribed decks with mixed scenarios."""
        # Setup deck names for mixed scenario
        ankihub_deck_names = [
            # New decks
            "Deck name A",
            "Deck name B",
            "Deck name C",
            "Deck name D",
            # Merged with same name
            "Deck name E",
            "Deck name F",
            "Deck name G",
            # Merged with different name
            "Deck name H",
            "Deck name I",
            "Deck name J",
        ]

        anki_deck_names = [
            # New decks (same as ankihub names)
            "Deck name A",
            "Deck name B",
            "Deck name C",
            "Deck name D",
            # Merged with same name
            "Deck name E",
            "Deck name F",
            "Deck name G",
            # Merged with different name
            "Existing Deck Name A",
            "Existing Deck Name B",
            "Existing Deck Name C",
        ]

        import_results = []
        # First 4 are new
        for _ in range(4):
            import_results.append(AnkiHubImportResultFactory.create(merged_with_existing_deck=False))
        # Next 6 are merged
        for _ in range(6):
            import_results.append(AnkiHubImportResultFactory.create(merged_with_existing_deck=True))

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=import_results,
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)

        # Check header
        assert "<b>Success!</b> Your decks are ready:" in message

        # Check new decks section
        assert "New deck(s) created (4 decks):" in message
        for letter in ["A", "B", "C", "D"]:
            assert f"<b>Deck name {letter}</b>" in message

        # Check merged same name section
        assert "Merged into existing deck(s) with matching names (3 decks):" in message
        for letter in ["E", "F", "G"]:
            assert f"<b>Deck name {letter}</b>" in message

        # Check merged different name section
        assert "Merged into existing deck(s) due to overlapping content (3 decks):" in message
        assert "<b>Deck name H</b> → <b>Existing Deck Name A</b>" in message
        assert "<b>Deck name I</b> → <b>Existing Deck Name B</b>" in message
        assert "<b>Deck name J</b> → <b>Existing Deck Name C</b>" in message

    def test_deck_with_skipped_notes(self, mock_dependencies: Dict[str, Any], qtbot: QtBot):
        """Test scenario: Deck with skipped notes warning."""
        ankihub_deck_names = ["Pathology Deck"]
        anki_deck_names = ["Pathology Deck"]

        import_result = AnkiHubImportResultFactory.create(
            merged_with_existing_deck=False,
            skipped_nids=[100, 101, 102, 103, 104],
        )

        _show_deck_import_summary_dialog_inner(
            ankihub_deck_names=ankihub_deck_names,
            anki_deck_names=anki_deck_names,
            import_results=[import_result],
        )

        if self.wait:
            qtbot.wait(self.wait)

        message = self.get_dialog_message(mock_dependencies)
        assert "Some notes were skipped" in message
        assert "share the same ID as notes in another AnkiHub deck" in message
        assert "see this topic" in message


class TestMoveSubdeckToMainDeck:
    """Tests for move_subdeck_to_main_deck function."""

    @patch("ankihub.main.block_exam_subdecks.note_ids_in_deck_hierarchy")
    @patch("ankihub.main.block_exam_subdecks.move_notes_to_decks_while_respecting_odid")
    @patch("ankihub.main.block_exam_subdecks.aqt")
    @patch("ankihub.main.block_exam_subdecks.config")
    def test_move_subdeck_to_main_deck_success(
        self,
        mock_config,
        mock_aqt,
        mock_move_notes,
        mock_note_ids_in_deck_hierarchy,
    ):
        """Test successfully moving subdeck to main deck."""
        # Setup mocks
        mock_subdeck = {"name": "Test Deck::Subdeck", "id": 456}
        mock_aqt.mw.col.decks.get.return_value = mock_subdeck
        mock_note_ids_in_deck_hierarchy.return_value = [1, 2, 3]

        # Mock the parent deck - parents() returns list of parent deck dictionaries
        mock_parent_deck = {"name": "Test Deck", "id": 123}
        mock_aqt.mw.col.decks.parents.return_value = [mock_parent_deck]

        subdeck_config = BlockExamSubdeckConfig(subdeck_id=DeckId(456), due_date="2024-12-31")
        mock_config.get_block_exam_subdeck_config.return_value = subdeck_config

        result = move_subdeck_to_main_deck(DeckId(456))

        assert result == 3  # Should return the number of notes moved
        mock_note_ids_in_deck_hierarchy.assert_called_once_with(456)
        mock_move_notes.assert_called_once_with({1: 123, 2: 123, 3: 123})
        mock_aqt.mw.col.decks.remove.assert_called_once_with([456])
        mock_config.remove_block_exam_subdeck.assert_called_once_with(DeckId(456))

    @patch("ankihub.main.block_exam_subdecks.aqt")
    @patch("ankihub.main.block_exam_subdecks.config")
    def test_move_subdeck_to_main_deck_subdeck_not_found(
        self,
        mock_config,
        mock_aqt,
    ):
        """Test handling when subdeck not found in Anki."""
        mock_aqt.mw.col.decks.get.return_value = False

        subdeck_config = BlockExamSubdeckConfig(subdeck_id=DeckId(456), due_date="2024-12-31")
        mock_config.get_block_exam_subdeck_config.return_value = subdeck_config

        result = move_subdeck_to_main_deck(DeckId(456))

        assert result == 0  # Should return 0 when subdeck not found
        mock_config.remove_block_exam_subdeck.assert_called_once_with(DeckId(456))


class TestSetSubdeckDueDate:
    """Tests for set_subdeck_due_date function."""

    @patch("ankihub.main.block_exam_subdecks.aqt")
    @patch("ankihub.main.block_exam_subdecks.config")
    def test_set_subdeck_due_date_success(self, mock_config, mock_aqt):
        """Test successfully setting a new due date."""
        mock_aqt.mw.col.decks.get.return_value = {"name": "Test Subdeck"}  # Mock subdeck exists

        # Mock existing config to return an old due date
        existing_config = BlockExamSubdeckConfig(
            subdeck_id=DeckId(456),
            due_date="2024-12-31",
            config_origin=BlockExamSubdeckOrigin.SMART_SEARCH,
        )
        mock_config.get_block_exam_subdeck_config.return_value = existing_config

        set_subdeck_due_date(DeckId(456), "2025-06-15", origin_hint=BlockExamSubdeckOrigin.DECK_CONTEXT_MENU)

        mock_config.upsert_block_exam_subdeck.assert_called_once_with(
            DeckId(456), due_date="2025-06-15", origin_hint=BlockExamSubdeckOrigin.DECK_CONTEXT_MENU
        )


class TestShowSubdeckDueDateReminder:
    """Tests for show_subdeck_due_date_reminder function."""

    @patch("ankihub.gui.subdeck_due_date_dialog.get_subdeck_log_context")
    @patch("ankihub.gui.subdeck_due_date_dialog.SubdeckDueDateReminderDialog")
    @patch("ankihub.gui.subdeck_due_date_dialog.aqt")
    def test_show_subdeck_due_date_reminder_success(self, mock_aqt, mock_dialog_class, mock_get_log_context):
        """Test successfully showing a due date reminder for an expired subdeck."""
        subdeck_name = "Exam Subdeck"
        mock_subdeck = {"name": f"Test Deck::{subdeck_name}", "id": 456}
        mock_aqt.mw.col.decks.get.return_value = mock_subdeck

        mock_dialog = MagicMock()
        mock_dialog_class.return_value = mock_dialog

        mock_get_log_context.return_value = {}

        subdeck_config = BlockExamSubdeckConfig(subdeck_id=DeckId(456), due_date="2024-12-31")

        show_subdeck_due_date_reminder(subdeck_config)

        mock_dialog_class.assert_called_once_with(subdeck_config, parent=mock_aqt.mw)
        mock_dialog.show.assert_called_once()

    @patch("ankihub.gui.subdeck_due_date_dialog.config", create=True)
    @patch("ankihub.gui.subdeck_due_date_dialog.aqt")
    def test_show_subdeck_due_date_reminder_not_found(self, mock_aqt, mock_config):
        """Test handling when expired subdeck not found in Anki."""
        mock_aqt.mw.col.decks.get.return_value = False

        subdeck_config = BlockExamSubdeckConfig(subdeck_id=DeckId(456), due_date="2024-12-31")

        show_subdeck_due_date_reminder(subdeck_config)

        mock_config.remove_block_exam_subdeck.assert_called_once_with(DeckId(456))


class TestShowSubdeckDueDateReminders:
    """Tests for show_subdeck_due_date_reminders function."""

    @patch("ankihub.gui.subdeck_due_date_dialog.show_subdeck_due_date_reminder")
    @patch("ankihub.gui.subdeck_due_date_dialog.get_expired_block_exam_subdecks")
    def test_show_subdeck_due_date_reminders_with_no_expired_subdecks(
        self,
        mock_check_due_dates,
        mock_show_reminder,
        set_feature_flag_state: SetFeatureFlagState,
    ):
        """Test function does not show any reminders when no subdecks are expired."""
        set_feature_flag_state("block_exam_subdecks", is_active=True)
        mock_check_due_dates.return_value = []

        maybe_show_subdeck_due_date_reminders()

        mock_check_due_dates.assert_called_once()
        mock_show_reminder.assert_not_called()

    @patch("ankihub.gui.subdeck_due_date_dialog.get_expired_block_exam_subdecks")
    @patch("ankihub.gui.subdeck_due_date_dialog.show_subdeck_due_date_reminder")
    def test_show_subdeck_due_date_reminders_with_expired_subdecks(
        self,
        mock_show_reminder,
        mock_check_due_dates,
        set_feature_flag_state: SetFeatureFlagState,
    ):
        """Test function shows reminder for first expired subdeck, queues the rest, and processes them sequentially."""
        set_feature_flag_state("block_exam_subdecks", is_active=True)

        from ankihub.gui.subdeck_due_date_dialog import (
            _due_date_reminder_dialog_state,
            _show_next_due_date_reminder_dialog,
        )

        expired_subdecks = [
            BlockExamSubdeckConfig(subdeck_id=DeckId(1), due_date="2023-01-01"),
            BlockExamSubdeckConfig(subdeck_id=DeckId(2), due_date="2023-01-02"),
        ]
        mock_check_due_dates.return_value = expired_subdecks

        maybe_show_subdeck_due_date_reminders()

        mock_check_due_dates.assert_called_once()
        # Only the first subdeck is handled immediately; the rest are queued
        assert mock_show_reminder.call_count == 1
        # Check the actual call argument
        actual_call_arg = mock_show_reminder.call_args[0][0]
        assert actual_call_arg.subdeck_id == DeckId(1)
        assert actual_call_arg.due_date == "2023-01-01"
        # Verify the second subdeck is still in the queue waiting to be shown
        assert len(_due_date_reminder_dialog_state.queue) == 1
        assert _due_date_reminder_dialog_state.queue[0].subdeck_id == DeckId(2)

        # Simulate the first dialog finishing
        _show_next_due_date_reminder_dialog()

        # Now the second subdeck should have been handled and queue should be empty
        assert mock_show_reminder.call_count == 2
        assert len(_due_date_reminder_dialog_state.queue) == 0

        # Calling again should do nothing (queue is empty)
        _show_next_due_date_reminder_dialog()
        assert mock_show_reminder.call_count == 2  # Should not increment
