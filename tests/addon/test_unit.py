import importlib
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Generator, List
from unittest.mock import Mock

import pytest
from anki.decks import DeckId
from anki.models import NotetypeDict
from anki.notes import Note, NoteId
from aqt import utils
from aqt.qt import QDialogButtonBox
from pytest import MonkeyPatch, fixture
from pytest_anki import AnkiSession
from pytestqt.qtbot import QtBot  # type: ignore

from ..factories import NoteInfoFactory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub import errors, suggestions
from ankihub.ankihub_client import AnkiHubHTTPError, Field, SuggestionType
from ankihub.db.db import MEDIA_DISABLED_FIELD_BYPASS_TAG, _AnkiHubDB
from ankihub.deck_creation import _note_type_name_without_ankihub_modifications
from ankihub.errors import (
    OUTDATED_CLIENT_ERROR_REASON,
    _contains_path_to_this_addon,
    _normalize_url,
    _try_handle_exception,
)
from ankihub.exporting import _prepared_field_html
from ankihub.gui.error_dialog import ErrorDialog
from ankihub.gui.menu import AnkiHubLogin
from ankihub.gui.suggestion_dialog import (
    SourceType,
    SuggestionDialog,
    SuggestionMetadata,
    SuggestionSource,
)
from ankihub.importing import _updated_tags
from ankihub.note_conversion import (
    ADDON_INTERNAL_TAGS,
    TAG_FOR_OPTIONAL_TAGS,
    TAG_FOR_PROTECTING_ALL_FIELDS,
    TAG_FOR_PROTECTING_FIELDS,
    _get_fields_protected_by_tags,
)
from ankihub.settings import ANKIWEB_ID
from ankihub.subdecks import SUBDECK_TAG, add_subdeck_tags_to_notes
from ankihub.threading_utils import rate_limited
from ankihub.utils import lowest_level_common_ancestor_deck_name, mids_of_notes


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
        ],
    )
    def test_visibility_of_form_elements_and_form_result(
        self,
        is_new_note_suggestion: bool,
        is_for_anking_deck: bool,
        suggestion_type: SuggestionType,
        source_type: SourceType,
        media_was_added: bool,
    ):
        dialog = SuggestionDialog(
            is_for_anking_deck=is_for_anking_deck,
            is_new_note_suggestion=is_new_note_suggestion,
            added_new_media=media_was_added,
        )
        dialog.show()

        # Fill in the form
        change_type_needed = not is_new_note_suggestion
        source_needed = not is_new_note_suggestion and (
            suggestion_type
            in [SuggestionType.UPDATED_CONTENT, SuggestionType.NEW_CONTENT]
            and is_for_anking_deck
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

        assert dialog.suggestion_meta() == SuggestionMetadata(
            comment="test",
            change_type=suggestion_type if change_type_needed else None,
            source=expected_source,
        )


class TestAnkiHubDBAnkiNidsToAnkiHubNids:
    def test_anki_nids_to_ankihub_nids(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        existing_anki_nid = 1
        non_existing_anki_nid = 2

        note = NoteInfoFactory.create(
            anki_nid=existing_anki_nid,
        )

        # Add a note to the DB.
        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        # Retrieve a dict of anki_nid -> ankihub_note_uuid for two anki_nids.
        ah_nids_for_anki_nids = ankihub_db.anki_nids_to_ankihub_nids(
            anki_nids=[NoteId(existing_anki_nid), NoteId(non_existing_anki_nid)]
        )

        assert ah_nids_for_anki_nids == {
            existing_anki_nid: note.ankihub_note_uuid,
            non_existing_anki_nid: None,
        }


class TestAnkiHubDBAnkiHubNidsToAnkiIds:
    def test_ankihub_nids_to_anki_ids(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        anki_nid = 1

        existing_ah_nid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            anki_nid=anki_nid,
            ankihub_note_uuid=existing_ah_nid,
        )
        ankihub_db.upsert_notes_data(
            ankihub_did=next_deterministic_uuid(),
            notes_data=[note],
        )

        not_exiisting_ah_nid = next_deterministic_uuid()

        # Retrieve a dict of anki_nid -> ankihub_note_uuid for two anki_nids.
        ah_nids_for_anki_nids = ankihub_db.ankihub_nids_to_anki_nids(
            ankihub_nids=[existing_ah_nid, not_exiisting_ah_nid]
        )

        assert ah_nids_for_anki_nids == {
            existing_ah_nid: anki_nid,
            not_exiisting_ah_nid: None,
        }


class TestAnkiHubDBRemoveNotes:
    def test_remove_notes(
        self,
        ankihub_db: _AnkiHubDB,
        next_deterministic_uuid: Callable[[], uuid.UUID],
    ):
        ankihub_note_uuid = next_deterministic_uuid()
        note = NoteInfoFactory.create(
            ankihub_note_uuid=ankihub_note_uuid,
        )

        ah_did = next_deterministic_uuid()
        ankihub_db.upsert_notes_data(
            ankihub_did=ah_did,
            notes_data=[note],
        )

        assert ankihub_db.anki_nids_for_ankihub_deck(ah_did) == [note.anki_nid]

        ankihub_db.remove_notes(
            ankihub_note_uuids=[ankihub_note_uuid],
        )

        assert ankihub_db.anki_nids_for_ankihub_deck(ankihub_did=ah_did) == []


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
        ankihub_db.upsert_notes_data(self.ah_did, [note_info])

    def test_basic(
        self,
        anki_session: AnkiSession,
        ankihub_db: _AnkiHubDB,
    ):
        with anki_session.profile_loaded():
            # Assert that the media name is returned for the field that is not disabled
            # and the media name is not returned for the field that is disabled.
            assert ankihub_db.media_names_for_ankihub_deck(
                self.ah_did, media_disabled_fields={}
            ) == {
                "test1.jpg",
                "test2.jpg",
                "test3.mp3",
            }

    def test_with_media_disabled_field(
        self,
        anki_session: AnkiSession,
        ankihub_db: _AnkiHubDB,
    ):
        with anki_session.profile_loaded():
            # Assert that the media name is returned for the field that is not disabled
            # and the media name is not returned for the field that is disabled.
            assert ankihub_db.media_names_for_ankihub_deck(
                self.ah_did, media_disabled_fields={self.mid: ["Front"]}
            ) == {"test2.jpg", "test3.mp3"}

    def test_bypass_using_media_disabled_bypass_tag(
        self,
        anki_session: AnkiSession,
        ankihub_db: _AnkiHubDB,
    ):
        with anki_session.profile_loaded():
            # Set bypass tag
            bypass_tag = f"{MEDIA_DISABLED_FIELD_BYPASS_TAG}::Front"
            sql = f"UPDATE notes SET tags = '{bypass_tag}' WHERE ankihub_deck_id = '{str(self.ah_did)}';"
            ankihub_db.execute(sql=sql)

            assert ankihub_db.media_names_for_ankihub_deck(
                self.ah_did, media_disabled_fields={self.mid: ["Front", "Back"]}
            ) == {"test1.jpg"}

    def test_with_notetype_missing_from_anki_db(
        self,
        anki_session: AnkiSession,
        ankihub_db: _AnkiHubDB,
    ):
        with anki_session.profile_loaded():
            mw = anki_session.mw
            mw.col.models.remove(self.mid)

            # Without the notetype in the Anki DB, the AnkiHub DB currently doesn't know the
            # names of the fields, so it just returns an empty set.
            # Assert that no error is raised and an empty set is returned.
            assert (
                ankihub_db.media_names_for_ankihub_deck(
                    self.ah_did, media_disabled_fields={}
                )
                == set()
            )


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

    def test_handle_ankihub_401(
        self,
        monkeypatch: MonkeyPatch,
    ):
        # Set up mock for AnkiHub login dialog.
        display_login_mock = Mock()
        monkeypatch.setattr(AnkiHubLogin, "display_login", display_login_mock)

        # Mock _this_addon_is_involved to return True.
        monkeypatch.setattr(errors, "_this_addon_mentioned_in_tb", lambda *args: True)

        handled = _try_handle_exception(
            exc_type=AnkiHubHTTPError,
            exc_value=AnkiHubHTTPError(response=Mock(status_code=401)),
            tb=None,
        )
        assert handled
        display_login_mock.assert_called_once()

    def test_handle_ankihub_406(
        self,
        monkeypatch: MonkeyPatch,
        _mock_ask_user_to_return_false: Mock,
    ):
        # Mock _this_addon_is_involved to return True.
        monkeypatch.setattr(errors, "_this_addon_mentioned_in_tb", lambda *args: True)

        handled = _try_handle_exception(
            exc_type=AnkiHubHTTPError,
            exc_value=AnkiHubHTTPError(
                response=Mock(status_code=406, reason=OUTDATED_CLIENT_ERROR_REASON)
            ),
            tb=None,
        )
        assert handled
        _mock_ask_user_to_return_false.assert_called_once()

    @fixture
    def _mock_ask_user_to_return_false(
        self,
        monkeypatch: MonkeyPatch,
    ) -> Generator[Mock, None, None]:
        # Simply monkeypatching askUser to return False doesn't work because the errors module
        # already imported the original askUser function when this fixture is called.
        # So we need to reload the errors module after monkeypatching askUser.
        try:
            with monkeypatch.context() as m:
                askUser_mock = Mock(return_value=False)
                m.setattr(utils, "askUser", askUser_mock)
                # Reload the errors module so that the monkeypatched askUser function is used.
                importlib.reload(errors)

                yield askUser_mock
        finally:
            #  Reload the errors module again so that the original askUser function is used for other tests.
            importlib.reload(errors)


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


def test_error_dialog(qtbot: QtBot, monkeypatch: MonkeyPatch):
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
    open_link_mock = Mock()
    monkeypatch.setattr(utils, "openLink", open_link_mock)
    dialog.button_box.button(QDialogButtonBox.StandardButton.Yes).click()
    open_link_mock.assert_called_once()

    # Check that clicking the No button does not throw an exception.
    dialog.button_box.button(QDialogButtonBox.StandardButton.No).click()
