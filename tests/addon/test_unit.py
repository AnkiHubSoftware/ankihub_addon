import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent
from typing import Callable, Generator, List, Optional, Protocol, Tuple
from unittest.mock import Mock, patch

import aqt
import pytest
from anki.decks import DeckId
from anki.models import NotetypeDict
from anki.notes import Note, NoteId
from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMenu,
    Qt,
    QTimer,
    QWidget,
)
from pytest import MonkeyPatch
from pytest_anki import AnkiSession
from pytest_mock import MockerFixture
from pytestqt.qtbot import QtBot  # type: ignore
from requests import Response
from requests_mock import Mocker

from ankihub.ankihub_client.ankihub_client import DEFAULT_API_URL
from ankihub.ankihub_client.models import (  # type: ignore
    CardReviewData,
    UserDeckExtensionRelation,
    UserDeckRelation,
)
from ankihub.gui import menu
from ankihub.gui.config_dialog import setup_config_dialog_manager
from ankihub.gui.configure_deleted_notes_dialog import ConfigureDeletedNotesDialog

from ..factories import (
    DeckExtensionFactory,
    DeckFactory,
    DeckMediaFactory,
    NoteInfoFactory,
)
from ..fixtures import (  # type: ignore
    AddAnkiNote,
    ImportAHNoteType,
    InstallAHDeck,
    LatestInstanceTracker,
    MockStudyDeckDialogWithCB,
    MockSuggestionDialog,
    SetFeatureFlagState,
    add_basic_anki_note_to_deck,
    assert_datetime_equal_ignore_milliseconds,
    create_anki_deck,
    record_review_for_anki_nid,
)
from .test_integration import ImportAHNote

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.addon_ankihub_client import AddonAnkiHubClient
from ankihub.ankihub_client import (
    AnkiHubClient,
    AnkiHubHTTPError,
    Field,
    SuggestionType,
    TagGroupValidationResponse,
)
from ankihub.db.db import _AnkiHubDB
from ankihub.db.exceptions import IntegrityError
from ankihub.db.models import AnkiHubNote, DeckMedia, get_peewee_database
from ankihub.feature_flags import _FeatureFlags, feature_flags
from ankihub.gui.error_dialog import ErrorDialog
from ankihub.gui.errors import (
    OUTDATED_CLIENT_ERROR_REASON,
    _contains_path_to_this_addon,
    _normalize_url,
    _try_handle_exception,
    upload_logs_in_background,
)
from ankihub.gui.media_sync import media_sync
from ankihub.gui.menu import AnkiHubLogin, menu_state, refresh_ankihub_menu
from ankihub.gui.operations.deck_creation import (
    DeckCreationConfirmationDialog,
    create_collaborative_deck,
)
from ankihub.gui.operations.utils import future_with_exception, future_with_result
from ankihub.gui.optional_tag_suggestion_dialog import OptionalTagsSuggestionDialog
from ankihub.gui.suggestion_dialog import (
    SourceType,
    SuggestionDialog,
    SuggestionMetadata,
    SuggestionSource,
    _on_suggest_notes_in_bulk_done,
    get_anki_nid_to_possible_ah_dids_dict,
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
from ankihub.main.deck_creation import (
    DeckCreationResult,
    _note_type_name_without_ankihub_modifications,
)
from ankihub.main.exporting import _prepared_field_html
from ankihub.main.importing import _updated_tags
from ankihub.main.note_conversion import (
    ADDON_INTERNAL_TAGS,
    TAG_FOR_OPTIONAL_TAGS,
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    _get_fields_protected_by_tags,
)
from ankihub.main.review_data import (
    _get_first_and_last_review_datetime_for_ah_deck,
    _get_review_count_for_ah_deck_since,
    send_review_data,
)
from ankihub.main.subdecks import (
    SUBDECK_TAG,
    add_subdeck_tags_to_notes,
    deck_contains_subdeck_tags,
)
from ankihub.main.suggestions import ChangeSuggestionResult
from ankihub.main.utils import (
    clear_empty_cards,
    lowest_level_common_ancestor_deck_name,
    mids_of_notes,
    retain_nids_with_ah_note_type,
)
from ankihub.settings import (
    ANKIWEB_ID,
    BehaviorOnRemoteNoteDeleted,
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
    def test_update_media_names_on_notes(
        self, anki_session_with_addon_data: AnkiSession
    ):
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

            assert f'<img src="{hashed_name_map["test.png"]}">' in " ".join(
                notes[0].fields
            )
            assert (
                f"<img src='{hashed_name_map['other_test.gif']}' width='250'>"
                in " ".join(notes[1].fields)
            )
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


class TestGetFieldsProtectedByTags:
    def test_protecting_single_fields(self):
        assert set(
            _get_fields_protected_by_tags(
                tags=[
                    f"{TAG_FOR_PROTECTING_FIELDS}::Text",
                    f"{TAG_FOR_PROTECTING_FIELDS}::Missed_Questions",
                ],
                field_names=["Text", "Extra", "Missed Questions", "Lecture Notes"],
            )
        ) == set(["Text", "Missed Questions"])

    def test_trying_to_protect_not_existing_field(self):
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

    def test_protecting_all_fields(self):
        assert set(
            _get_fields_protected_by_tags(
                tags=[TAG_FOR_PROTECTING_ALL_FIELDS],
                field_names=["Text", "Extra", "Missed Questions", "Lecture Notes"],
            )
        ) == set(["Text", "Extra", "Missed Questions", "Lecture Notes"])


def test_normalize_url():
    url = "https://app.ankihub.net/api/decks/fc39e7e7-9705-4102-a6ec-90d128c64ed3/updates?since=2022-08-01T1?6%3A32%3A2"
    assert _normalize_url(url) == "https://app.ankihub.net/api/decks/<id>/updates"

    url = "https://app.ankihub.net/api/note-types/2385223452/"
    assert _normalize_url(url) == "https://app.ankihub.net/api/note-types/<id>/"


def test_prepared_field_html():
    assert _prepared_field_html('<img src="foo.jpg">') == '<img src="foo.jpg">'

    assert (
        _prepared_field_html('<img src="foo.jpg" data-editor-shrink="true">')
        == '<img src="foo.jpg">'
    )


def test_remove_note_type_name_modifications():
    name = "Basic (deck_name / user_name)"
    assert _note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name / user_name) (deck_name2 / user_name2)"
    assert _note_type_name_without_ankihub_modifications(name) == "Basic"

    name = "Basic (deck_name/user_name)"
    assert _note_type_name_without_ankihub_modifications(name) == name


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

            note_info = NoteInfoFactory.create(
                tags=["some_other_tag", f"{SUBDECK_TAG}::A::B"]
            )
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
    @pytest.mark.parametrize(
        "confirmed_sign_out,expected_logged_in_state", [(True, False), (False, True)]
    )
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
            config._private_config.token = user_token

            mw = anki_session.mw
            menu_state.ankihub_menu = QMenu("&AnkiHub", parent=aqt.mw)
            mw.form.menubar.addMenu(menu_state.ankihub_menu)
            setup_config_dialog_manager()
            refresh_ankihub_menu()

            sign_out_action = [
                action
                for action in menu_state.ankihub_menu.actions()
                if action.text() == "🔑 Sign out"
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

        login_mock = mocker.patch(
            "ankihub.gui.menu.AnkiHubClient.login", return_value=token
        )

        AnkiHubLogin.display_login()

        window: AnkiHubLogin = AnkiHubLogin._window

        window.username_or_email_box_text.setText(username)
        window.password_box_text.setText(password)
        window.login_button.click()

        qtbot.wait_until(lambda: not window.isVisible())

        login_mock.assert_called_once_with(
            credentials={"username": username, "password": password}
        )

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
        qtbot.wait_until(
            lambda: window.password_box_text.echoMode() == QLineEdit.EchoMode.Normal
        )

        assert window.password_box_text.echoMode() == QLineEdit.EchoMode.Normal
        assert window.toggle_button.isChecked() is True

        window.toggle_button.click()
        qtbot.wait_until(
            lambda: window.password_box_text.echoMode() == QLineEdit.EchoMode.Password
        )

        assert window.password_box_text.echoMode() == QLineEdit.EchoMode.Password
        assert window.toggle_button.isChecked() is False

    @patch("ankihub.gui.menu.AnkiHubClient.login")
    def test_forgot_password_and_sign_up_links_are_present(
        self, login_mock, qtbot: QtBot
    ):
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
            (
                suggestion_type
                in [SuggestionType.UPDATED_CONTENT, SuggestionType.NEW_CONTENT]
                and is_for_anking_deck
            )
            or suggestion_type == SuggestionType.DELETE
        )

        if change_type_needed:
            dialog.change_type_select.setCurrentText(suggestion_type.value[1])

        expected_source_text = ""
        if source_needed:
            dialog.source_widget.source_type_select.setCurrentText(source_type.value)
            if source_type == SourceType.UWORLD:
                expected_uworld_step = "Step 1"
                dialog.source_widget.uworld_step_select.setCurrentText(
                    expected_uworld_step
                )

            expected_source_text = "https://test_url.com"
            dialog.source_widget.source_edit.setText(expected_source_text)

        dialog.rationale_edit.setPlainText("test")

        # Assert that correct form elements are shown
        assert dialog.isVisible()

        if change_type_needed:
            assert dialog.change_type_select.isVisible()
        else:
            assert not dialog.change_type_select.isVisible()

        if source_needed:
            assert dialog.source_widget_group_box.isVisible()
        else:
            assert not dialog.source_widget_group_box.isVisible()

        # Assert that the form submit button is enabled (it is disabled if the form input is invalid)
        assert dialog.button_box.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

        # Assert that the form result is correct
        expected_source_text = (
            f"{expected_uworld_step} {expected_source_text}"
            if source_type == SourceType.UWORLD
            else expected_source_text
        )
        expected_source = (
            SuggestionSource(source_type=source_type, source_text=expected_source_text)
            if source_needed
            else None
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
    def test_submit_without_review_checkbox(
        self, can_submit_without_review: bool, mocker: MockerFixture
    ):
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


class TestSuggestionDialogGetAnkiNidToPossibleAHDidsDict:
    def test_with_existing_note_belonging_to_single_deck(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            note_info = import_ah_note(ah_did=ah_did)
            nids = [NoteId(note_info.anki_nid)]
            assert get_anki_nid_to_possible_ah_dids_dict(nids) == {
                note_info.anki_nid: {ah_did}
            }

    def test_with_new_note_belonging_to_single_deck(
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
            assert get_anki_nid_to_possible_ah_dids_dict(nids) == {note.id: {ah_did}}

    def test_with_new_note_with_two_possible_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = next_deterministic_uuid()
            note_type = import_ah_note_type(ah_did=ah_did_1)

            ah_did_2 = next_deterministic_uuid()
            import_ah_note_type(note_type=note_type, ah_did=ah_did_2)

            # The note type of the new note is used in two decks, so the note could be suggested for either of them.
            note = add_anki_note(note_type=note_type)
            nids = [note.id]
            assert get_anki_nid_to_possible_ah_dids_dict(nids) == {
                note.id: {ah_did_1, ah_did_2}
            }

    def test_with_existing_note_with_note_type_used_in_two_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        import_ah_note: ImportAHNote,
        import_ah_note_type: ImportAHNoteType,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = next_deterministic_uuid()
            note_type = import_ah_note_type(ah_did=ah_did_1)
            note_info = import_ah_note(ah_did=ah_did_1, mid=note_type["id"])

            ah_did_2 = next_deterministic_uuid()
            import_ah_note_type(note_type=note_type, ah_did=ah_did_2)

            # The note type of the new note is used in two decks, but the note exists in one of them,
            # so the note belongs to that deck.
            nids = [NoteId(note_info.anki_nid)]
            assert get_anki_nid_to_possible_ah_dids_dict(nids) == {
                note_info.anki_nid: {ah_did_1}
            }


class MockDependenciesForSuggestionDialog(Protocol):
    def __call__(self, user_cancels: bool) -> Tuple[Mock, Mock]:
        ...


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

        suggest_note_update_mock = mocker.patch(
            "ankihub.gui.suggestion_dialog.suggest_note_update"
        )
        suggest_new_note_mock = mocker.patch(
            "ankihub.gui.suggestion_dialog.suggest_new_note"
        )

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
    def test_with_existing_note_belonging_to_single_deck(
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
                ChangeSuggestionResult.SUCCESS
                if suggest_note_update_succeeds
                else ChangeSuggestionResult.NO_CHANGES
            )

            open_suggestion_dialog_for_single_suggestion(note=note, parent=aqt.mw)

            if user_cancels:
                suggest_note_update_mock.assert_not_called()
                suggest_new_note_mock.assert_not_called()
            else:
                _, kwargs = suggest_note_update_mock.call_args
                assert kwargs.get("note") == note

                suggest_new_note_mock.assert_not_called()

    @pytest.mark.parametrize(
        "user_cancels",
        [
            True,
            False,
        ],
    )
    def test_with_new_note_which_could_belong_to_two_decks(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        mock_dependiencies_for_suggestion_dialog: MockDependenciesForSuggestionDialog,
        mocker: MockerFixture,
        user_cancels: bool,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_type = import_ah_note_type(ah_did=ah_did_1)

            # Add the note type to a second deck
            ah_did_2 = install_ah_deck()
            import_ah_note_type(ah_did=ah_did_2, note_type=note_type)

            note = add_anki_note(note_type=note_type)

            (
                suggest_note_update_mock,
                suggest_new_note_mock,
            ) = mock_dependiencies_for_suggestion_dialog(user_cancels=False)

            choose_ankihub_deck_mock = mocker.patch(
                "ankihub.gui.suggestion_dialog.choose_ankihub_deck",
                return_value=None if user_cancels else ah_did_1,
            )

            open_suggestion_dialog_for_single_suggestion(note=note, parent=aqt.mw)

            if user_cancels:
                suggest_note_update_mock.assert_not_called()
                suggest_new_note_mock.assert_not_called()
            else:
                # There are two options for the deck, so the user has to choose one.
                _, kwargs = choose_ankihub_deck_mock.call_args
                assert kwargs.get("ah_dids") == [ah_did_1, ah_did_2]

                # The note should be suggested for the chosen deck.
                _, kwargs = suggest_new_note_mock.call_args
                assert kwargs.get("note") == note

                suggest_note_update_mock.assert_not_called()


class MockDependenciesForBulkSuggestionDialog(Protocol):
    def __call__(self, user_cancels: bool) -> Mock:
        ...


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
    def test_with_existing_note_belonging_to_single_deck(
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

            suggest_notes_in_bulk_mock = mock_dependencies_for_bulk_suggestion_dialog(
                user_cancels=user_cancels
            )

            open_suggestion_dialog_for_bulk_suggestion(anki_nids=nids, parent=aqt.mw)

            if user_cancels:
                qtbot.wait(500)
                suggest_notes_in_bulk_mock.assert_not_called()
            else:
                qtbot.wait_until(lambda: suggest_notes_in_bulk_mock.called)
                _, kwargs = suggest_notes_in_bulk_mock.call_args
                assert kwargs.get("ankihub_did") == ah_did
                assert {note.id for note in kwargs.get("notes")} == set(nids)

    def test_with_two_new_notes_without_decks_in_common(
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

            suggest_notes_in_bulk_mock = mock_dependencies_for_bulk_suggestion_dialog(
                user_cancels=False
            )

            open_suggestion_dialog_for_bulk_suggestion(anki_nids=nids, parent=aqt.mw)
            qtbot.wait(500)

            # The note suggestions can't be for the same deck, so the suggestion dialog should not be shown.
            suggest_notes_in_bulk_mock.assert_not_called()

    def test_with_two_new_notes_with_decks_in_common(
        self,
        anki_session_with_addon_data: AnkiSession,
        install_ah_deck: InstallAHDeck,
        import_ah_note_type: ImportAHNoteType,
        add_anki_note: AddAnkiNote,
        mock_dependencies_for_bulk_suggestion_dialog: MockDependenciesForBulkSuggestionDialog,
        mocker: MockerFixture,
        qtbot: QtBot,
    ):
        with anki_session_with_addon_data.profile_loaded():
            ah_did_1 = install_ah_deck()
            note_type = import_ah_note_type(ah_did=ah_did_1)
            note_1 = add_anki_note(note_type=note_type)

            ah_did_2 = install_ah_deck()
            import_ah_note_type(ah_did=ah_did_2, note_type=note_type)
            note_2 = add_anki_note(note_type=note_type)

            nids = [note_1.id, note_2.id]

            choose_ankihub_deck_mock = mocker.patch(
                "ankihub.gui.suggestion_dialog.choose_ankihub_deck",
                return_value=ah_did_1,
            )
            suggest_notes_in_bulk_mock = mock_dependencies_for_bulk_suggestion_dialog(
                user_cancels=False
            )

            open_suggestion_dialog_for_bulk_suggestion(anki_nids=nids, parent=aqt.mw)
            qtbot.wait_until(lambda: suggest_notes_in_bulk_mock.called)

            # There are two options for the deck the note suggestions can be for, so the user should be asked
            # to choose between them.
            _, kwargs = choose_ankihub_deck_mock.call_args
            assert kwargs.get("ah_dids") == [ah_did_1, ah_did_2]

            # After the user has chosen the deck, the suggestion dialog should be shown for the chosen deck.
            _, kwargs = suggest_notes_in_bulk_mock.call_args
            assert kwargs.get("ankihub_did") == ah_did_1
            assert {note.id for note in kwargs.get("notes")} == set(nids)


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


class TestAnkiHubDBAnkiNidsToAnkiHubNids:
    def test_anki_nids_to_ankihub_nids(
        self,
        ankihub_db: _AnkiHubDB,
        ankihub_basic_note_type: NotetypeDict,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        ah_did = next_deterministic_uuid()
        existing_anki_nid = 1
        non_existing_anki_nid = 2

        # Add a note to the DB.
        ankihub_db.upsert_note_type(
            ankihub_did=ah_did,
            note_type=ankihub_basic_note_type,
        )
        note = NoteInfoFactory.create(
            anki_nid=existing_anki_nid,
            mid=ankihub_basic_note_type["id"],
        )

        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        # Retrieve a dict of anki_nid -> ah_nid for two anki_nids.
        ah_nids_for_anki_nids = ankihub_db.anki_nids_to_ankihub_nids(
            anki_nids=[NoteId(existing_anki_nid), NoteId(non_existing_anki_nid)]
        )

        assert ah_nids_for_anki_nids == {
            existing_anki_nid: note.ah_nid,
            non_existing_anki_nid: None,
        }


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


class TestAnkiHubDBAreAnkiHubNotes:
    def test_with_one_ankihub_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_db: _AnkiHubDB,
        import_ah_note: ImportAHNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info = import_ah_note()
            assert ankihub_db.are_ankihub_notes(anki_nids=[NoteId(note_info.anki_nid)])

    def test_with_multiple_ankihub_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_db: _AnkiHubDB,
        import_ah_note: ImportAHNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            note_info_1 = import_ah_note()
            note_info_2 = import_ah_note()
            assert ankihub_db.are_ankihub_notes(
                anki_nids=[NoteId(note_info_1.anki_nid), NoteId(note_info_2.anki_nid)]
            )

    def test_with_one_non_ankihub_note(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_db: _AnkiHubDB,
        add_anki_note: AddAnkiNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            anki_note = add_anki_note()
            assert not ankihub_db.are_ankihub_notes(anki_nids=[anki_note.id])

    def test_with_mixed_notes(
        self,
        anki_session_with_addon_data: AnkiSession,
        ankihub_db: _AnkiHubDB,
        import_ah_note: ImportAHNote,
        add_anki_note: AddAnkiNote,
    ):
        with anki_session_with_addon_data.profile_loaded():
            anki_note = add_anki_note()
            note_info = import_ah_note()
            assert not ankihub_db.are_ankihub_notes(
                anki_nids=[anki_note.id, NoteId(note_info.anki_nid)]
            )


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
        assert len(ankihub_db.downloadable_media_names_for_ankihub_deck(ah_did)) == 1

        # Remove the deck
        ankihub_db.remove_deck(ankihub_did=ah_did)

        # Assert that everything is removed
        assert ankihub_db.anki_nids_for_ankihub_deck(ankihub_did=ah_did) == []
        assert ankihub_db.note_types_for_ankihub_deck(ankihub_did=ah_did) == []
        assert (
            ankihub_db.note_type_dict(
                ankihub_did=ah_did, note_type_id=ankihub_basic_note_type["id"]
            )
            is None
        )
        assert (
            ankihub_db.ankihub_dids_for_note_type(
                anki_note_type_id=ankihub_basic_note_type["id"]
            )
            is None
        )

        assert ankihub_db.downloadable_media_names_for_ankihub_deck(ah_did) == set()


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
                Field(value="test <img src='test1.jpg'>", order=0, name="Front"),
                Field(
                    value="test <img src='test2.jpg'> [sound:test3.mp3]",
                    order=1,
                    name="Back",
                ),
            ],
        )
        self.ah_did = next_deterministic_uuid()
        ankihub_db.upsert_note_type(
            ankihub_did=self.ah_did, note_type=ankihub_basic_note_type
        )
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
        with anki_session_with_addon_data.profile_loaded():
            ah_did = next_deterministic_uuid()
            ankihub_db.upsert_deck_media_infos(
                ankihub_did=ah_did,
                media_list=[
                    DeckMediaFactory.create(
                        name="test1.jpg",
                        referenced_on_accepted_note=referenced_on_accepted_note,
                        exists_on_s3=exists_on_s3,
                        download_enabled=download_enabled,
                    )
                ],
            )

            expected_result = (
                {"test1.jpg"}
                if referenced_on_accepted_note and exists_on_s3 and download_enabled
                else set()
            )
            assert (
                ankihub_db.downloadable_media_names_for_ankihub_deck(ah_did=ah_did)
                == expected_result
            )


class TestAnkiHubDBMediaNamesWithMatchingHashes:
    def test_get_matching_media(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
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

    def test_with_none_in_media_to_hash(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash="hash1"),
            ],
        )

        assert (
            ankihub_db.media_names_with_matching_hashes(
                ah_did=ah_did, media_to_hash={"test1_copy.jpg": None}
            )
            == {}
        )

    def test_with_none_in_db(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did,
            media_list=[
                DeckMediaFactory.create(name="test1.jpg", file_content_hash=None),
            ],
        )

        assert (
            ankihub_db.media_names_with_matching_hashes(
                ah_did=ah_did, media_to_hash={"test1_copy.jpg": "hash1"}
            )
            == {}
        )


class TestAnkiHubDBDeckMedia:
    def test_modified_field_is_stored_in_correct_format_in_db(
        self, ankihub_db: _AnkiHubDB, next_deterministic_uuid
    ):
        ah_did = next_deterministic_uuid()
        deck_media_from_client = DeckMediaFactory.create()

        ankihub_db.upsert_deck_media_infos(
            ankihub_did=ah_did, media_list=[deck_media_from_client]
        )

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
        assert _contains_path_to_this_addon(
            f"/addons21/{ANKIWEB_ID}/src/ankihub/errors.py"
        )

        # Same as above, but with Windows path separators.
        assert _contains_path_to_this_addon(
            "\\addons21\\ankihub\\src\\ankihub\\errors.py"
        )
        assert _contains_path_to_this_addon(
            f"\\addons21\\{ANKIWEB_ID}\\src\\ankihub\\errors.py"
        )

        # Assert that the function returns False when the input string does not contain
        # the path to this addon.
        assert not _contains_path_to_this_addon(
            "/addons21/other_addon/src/ankihub/errors.py"
        )
        assert not _contains_path_to_this_addon(
            "/addons21/12345789/src/ankihub/errors.py"
        )

        # Same as above, but with Windows path separators.
        assert not _contains_path_to_this_addon(
            "\\addons21\\other_addon\\src\\ankihub\\errors.py"
        )
        assert not _contains_path_to_this_addon(
            "\\addons21\\12345789\\src\\ankihub\\errors.py"
        )

    def test_handle_ankihub_401(self, mocker: MockerFixture):
        # Set up mock for AnkiHub login dialog.
        display_login_mock = mocker.patch.object(AnkiHubLogin, "display_login")

        handled = _try_handle_exception(
            exc_type=AnkiHubHTTPError,
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
        ],
    )
    def test_handle_ankihub_403(
        self, mocker: MockerFixture, response_content: str, expected_handled: bool
    ):
        show_error_dialog_mock = mocker.patch("ankihub.gui.errors.show_error_dialog")

        response_mock = mocker.Mock()
        response_mock.status_code = 403
        response_mock.text = response_content
        response_mock.json = lambda: json.loads(response_content)  # type: ignore

        handled = _try_handle_exception(
            exc_type=AnkiHubHTTPError,
            exc_value=AnkiHubHTTPError(response=response_mock),
            tb=None,
        )
        assert handled == expected_handled
        assert show_error_dialog_mock.called == expected_handled

    def test_handle_ankihub_406(self, mocker: MockerFixture):
        ask_user_mock = mocker.patch("ankihub.gui.errors.ask_user", return_value=False)
        handled = _try_handle_exception(
            exc_type=AnkiHubHTTPError,
            exc_value=AnkiHubHTTPError(
                response=Mock(status_code=406, reason=OUTDATED_CLIENT_ERROR_REASON)
            ),
            tb=None,
        )
        assert handled
        ask_user_mock.assert_called_once()


def test_show_error_dialog(
    anki_session_with_addon_data: AnkiSession, mocker: MockerFixture, qtbot: QtBot
):
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
                AnkiHubHTTPError(
                    response=Mock(status_code=406, reason=OUTDATED_CLIENT_ERROR_REASON)
                ),
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
        upload_logs_mock = mocker.patch.object(
            AddonAnkiHubClient, "upload_logs", side_effect=exception
        )
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
    def test_with_default_values(
        self,
        mock_all_feature_flags_to_default_values: None,
    ):
        for field in fields(_FeatureFlags):
            assert getattr(feature_flags, field.name) == field.default

    def test_with_set_values(
        self,
        set_feature_flag_state: SetFeatureFlagState,
    ):
        for field in fields(_FeatureFlags):
            set_feature_flag_state(field.name, False)
            assert not getattr(feature_flags, field.name)

            set_feature_flag_state(field.name, True)
            assert getattr(feature_flags, field.name)


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
    def __call__(self, deck_name: str) -> None:
        ...


@pytest.fixture
def mock_ui_for_create_collaborative_deck(
    mocker: MockerFixture,
    mock_study_deck_dialog_with_cb: MockStudyDeckDialogWithCB,
) -> MockUIForCreateCollaborativeDeck:
    """Mock the UI interaction for creating a collaborative deck.
    The deck_name determines which deck will be chosen for the upload."""

    def mock_ui_interaction_inner(deck_name) -> None:
        mock_study_deck_dialog_with_cb(
            "ankihub.gui.operations.deck_creation.StudyDeck", deck_name
        )
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
            start_media_upload_mock = mocker.patch.object(
                media_sync, "start_media_upload"
            )
            showInfo_mock = mocker.patch(
                "ankihub.gui.operations.deck_creation.showInfo"
            )

            # Create the AnkiHub deck.
            if creating_deck_fails:
                create_collaborative_deck()
                qtbot.wait(500)
                showInfo_mock.assert_not_called()
            else:
                create_collaborative_deck()

                qtbot.wait_until(lambda: showInfo_mock.called)

                # Assert that the correct functions were called.
                create_ankihub_deck_mock.assert_called_once_with(
                    deck_name, private=False, add_subdeck_tags=False
                )

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

            showInfo_mock = mocker.patch(
                "ankihub.gui.operations.deck_creation.showInfo"
            )
            create_ankihub_deck_mock = mocker.patch(
                "ankihub.gui.operations.deck_creation.create_ankihub_deck"
            )

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
                record_review_for_anki_nid(
                    NoteId(note_info.anki_nid), now + review_delta
                )

            assert (
                _get_review_count_for_ah_deck_since(
                    ah_did=ah_did, since=now + since_time
                )
                == expected_count
            )

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
            record_review_for_anki_nid(
                NoteId(note_info_2.anki_nid), now + timedelta(seconds=1)
            )

            since_time = now - timedelta(days=1)
            assert (
                _get_review_count_for_ah_deck_since(ah_did=ah_did, since=since_time)
                == 2
            )

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
            record_review_for_anki_nid(
                NoteId(note_info_2.anki_nid), now + timedelta(seconds=1)
            )

            # Only the review for the first deck should be counted.
            since_time = now - timedelta(days=1)
            assert (
                _get_review_count_for_ah_deck_since(ah_did=ah_did_1, since=since_time)
                == 1
            )


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
                record_review_for_anki_nid(
                    NoteId(note_info.anki_nid), now + review_delta
                )

            first_and_last_time = _get_first_and_last_review_datetime_for_ah_deck(
                ah_did=ah_did
            )

            if expected_last_review_delta is not None:
                first_review_time, last_review_time = first_and_last_time

                expected_first_review_time = now + expected_first_review_delta
                assert_datetime_equal_ignore_milliseconds(
                    first_review_time,
                    expected_first_review_time,
                )

                expected_last_review_time = now + expected_last_review_delta
                assert_datetime_equal_ignore_milliseconds(
                    last_review_time, expected_last_review_time
                )
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
            record_review_for_anki_nid(
                NoteId(note_info_1.anki_nid), expected_first_review_time
            )

            expected_last_review_time = expected_first_review_time + timedelta(days=1)
            record_review_for_anki_nid(
                NoteId(note_info_2.anki_nid), expected_last_review_time
            )

            (
                first_review_time,
                last_review_time,
            ) = _get_first_and_last_review_datetime_for_ah_deck(ah_did=ah_did)

            assert_datetime_equal_ignore_milliseconds(
                first_review_time,
                expected_first_review_time,
            )
            assert_datetime_equal_ignore_milliseconds(
                last_review_time, expected_last_review_time
            )

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
            record_review_for_anki_nid(
                NoteId(note_info_1.anki_nid), expected_review_time
            )
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

            send_card_review_data_mock = mocker.patch.object(
                AnkiHubClient, "send_card_review_data"
            )

            send_review_data()

            # Assert that the correct data was passed to the client method.
            send_card_review_data_mock.assert_called_once()

            card_review_data: CardReviewData = send_card_review_data_mock.call_args[0][
                0
            ][0]
            assert card_review_data.ah_did == ah_did
            assert card_review_data.total_card_reviews_last_7_days == 2
            assert card_review_data.total_card_reviews_last_30_days == 2
            assert_datetime_equal_ignore_milliseconds(
                card_review_data.first_card_review_at, first_review_time
            )
            assert_datetime_equal_ignore_milliseconds(
                card_review_data.last_card_review_at, second_review_time
            )

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

            send_card_review_data_mock = mocker.patch.object(
                AnkiHubClient, "send_card_review_data"
            )

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
        show_dialog(
            text="some text", title="some title", parent=dialog, scrollable=scrollable
        )

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
    def test_oprphaned_deck_extensions_are_removed(
        self, next_deterministic_uuid: Callable[[], uuid.UUID]
    ):
        # Add a deck extension without a corressponding deck to the private config.
        ah_did = next_deterministic_uuid()
        deck_extension = DeckExtensionFactory.create(ah_did=ah_did)
        config.create_or_update_deck_extension_config(deck_extension)

        # sanity check
        assert config.deck_extensions_ids_for_ah_did(ah_did) == [deck_extension.id]

        # Reload the private config to trigger the migration.
        config.setup_private_config()

        assert config.deck_extensions_ids_for_ah_did(ah_did) == []

    @pytest.mark.parametrize(
        "behavior_on_remote_note_deleted",
        [
            BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS,
            BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        ],
    )
    def test_maybe_prompt_user_for_behavior_on_remote_note_deleted(
        self,
        mocker: MockerFixture,
        behavior_on_remote_note_deleted: BehaviorOnRemoteNoteDeleted,
    ):
        deck = DeckFactory.create()
        config.add_deck(
            name=deck.name,
            ankihub_did=deck.ah_did,
            anki_did=DeckId(deck.anki_did),
            user_relation=UserDeckRelation.OWNER,
            behavior_on_remote_note_deleted=BehaviorOnRemoteNoteDeleted.NEVER_DELETE,
        )

        # Set the behavior_on_remote_note_deleted to None in the private config,
        # to simulate the old state.
        config.deck_config(deck.ah_did).behavior_on_remote_note_deleted = None
        config._update_private_config()

        mocker.patch.object(ConfigureDeletedNotesDialog, "exec")
        mocker.patch.object(
            ConfigureDeletedNotesDialog,
            "deck_id_to_behavior_on_remote_note_deleted_dict",
            return_value={deck.ah_did: behavior_on_remote_note_deleted},
        )

        assert config.deck_config(deck.ah_did).behavior_on_remote_note_deleted is None

        # Reload the private config to trigger the migration.
        config.setup_private_config()

        assert (
            config.deck_config(deck.ah_did).behavior_on_remote_note_deleted
            == behavior_on_remote_note_deleted
        )


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
            dialog = OptionalTagsSuggestionDialog(
                parent=widget, nids=[NoteId(note_info.anki_nid)]
            )

            # Mock the suggest_tags_for_groups method which is called when the submit button is clicked
            suggest_tags_for_groups_mock = mocker.patch.object(
                dialog._optional_tags_helper, "suggest_tags_for_groups"
            )

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
            dialog = OptionalTagsSuggestionDialog(
                parent=widget, nids=[NoteId(note_info.anki_nid)]
            )

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
        button = next(
            button
            for button in dialog.button_box.buttons()
            if text_of_button_to_click in button.text()
        )
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
            assert table_definitions == expected_table_definitions
            assert index_definitions == expected_index_definitions
            assert ankihub_db.database_path != migration_test_db_path  # sanity check


class TestConfigureDeletedNotesDialog:
    @pytest.mark.parametrize(
        "check_first_checkbox, check_second_checkbox",
        [(True, False), (False, True), (True, True), (False, False)],
    )
    def test_with_two_decks(
        self,
        next_deterministic_uuid,
        check_first_checkbox: bool,
        check_second_checkbox: bool,
    ):
        parent = QDialog()

        deck_1_id = next_deterministic_uuid()
        deck_1_name = "Deck 1"

        deck_2_id = next_deterministic_uuid()
        deck_2_name = "Deck 2"

        deck_id_name_tuples = [
            (deck_1_id, deck_1_name),
            (deck_2_id, deck_2_name),
        ]

        dialog = ConfigureDeletedNotesDialog(
            deck_id_and_name_tuples=deck_id_name_tuples,
            parent=parent,
        )
        dialog.show()

        # Check initial state
        label_for_deck_1 = dialog.grid_layout.itemAtPosition(1, 0).widget()
        assert label_for_deck_1.text() == deck_1_name

        label_for_deck_2 = dialog.grid_layout.itemAtPosition(2, 0).widget()
        assert label_for_deck_2.text() == deck_2_name

        checkbox_for_deck_1: QCheckBox = (
            dialog.grid_layout.itemAtPosition(1, 1).layout().itemAt(1).widget()
        )
        assert not checkbox_for_deck_1.isChecked()

        checkbox_for_deck_2: QCheckBox = (
            dialog.grid_layout.itemAtPosition(2, 1).layout().itemAt(1).widget()
        )
        assert not checkbox_for_deck_2.isChecked()

        # Check a checkbox
        if check_first_checkbox:
            checkbox_for_deck_1.setChecked(True)

        if check_second_checkbox:
            checkbox_for_deck_2.setChecked(True)

        # Check that the dialog returns the expected values
        assert dialog.deck_id_to_behavior_on_remote_note_deleted_dict() == {
            deck_1_id: (
                BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
                if check_first_checkbox
                else BehaviorOnRemoteNoteDeleted.NEVER_DELETE
            ),
            deck_2_id: (
                BehaviorOnRemoteNoteDeleted.DELETE_IF_NO_REVIEWS
                if check_second_checkbox
                else BehaviorOnRemoteNoteDeleted.NEVER_DELETE
            ),
        }

    def test_close_button_has_no_effect(self, next_deterministic_uuid):
        parent = QDialog()

        deck_1_id = next_deterministic_uuid()
        deck_1_name = "Test Deck"
        deck_id_name_tuples = [
            (deck_1_id, deck_1_name),
        ]

        dialog = ConfigureDeletedNotesDialog(
            deck_id_and_name_tuples=deck_id_name_tuples,
            parent=parent,
        )
        dialog.show()
        dialog.close()

        assert dialog.isVisible()

    def test_ok_button_closes_dialog(self, next_deterministic_uuid):
        parent = QDialog()

        deck_1_id = next_deterministic_uuid()
        deck_1_name = "Test Deck"
        deck_id_name_tuples = [
            (deck_1_id, deck_1_name),
        ]

        dialog = ConfigureDeletedNotesDialog(
            deck_id_and_name_tuples=deck_id_name_tuples,
            parent=parent,
        )
        dialog.show()
        dialog.button_box.button(QDialogButtonBox.Ok).click()

        assert not dialog.isVisible()

    @pytest.mark.parametrize(
        "show_new_feature_message",
        [True, False],
    )
    def test_show_new_feature_message(
        self, next_deterministic_uuid, show_new_feature_message: bool
    ):
        parent = QDialog()

        deck_1_id = next_deterministic_uuid()
        deck_1_name = "Test Deck"
        deck_id_name_tuples = [
            (deck_1_id, deck_1_name),
        ]

        dialog = ConfigureDeletedNotesDialog(
            deck_id_and_name_tuples=deck_id_name_tuples,
            parent=parent,
            show_new_feature_message=show_new_feature_message,
        )
        dialog.show()

        assert dialog.new_feature_label.isVisible() == show_new_feature_message
