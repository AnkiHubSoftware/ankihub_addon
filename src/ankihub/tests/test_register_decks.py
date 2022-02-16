import copy
import pathlib
from unittest.mock import Mock

from pytest_anki import AnkiSession

from ankihub import constants

ANKING_MODEL_ID = 1566160514431
anking_deck = str(pathlib.Path(__file__).parent / "test_data" / "anking.apkg")


def test_get_note_types_in_deck(anki_session: AnkiSession) -> None:
    """Check that get_note_types_in_deck returns the expected model id."""
    from ankihub.utils import get_note_types_in_deck

    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            note_mode_ids = get_note_types_in_deck(deck_id)
            # TODO test on a deck that has more than one note type.
            assert len(note_mode_ids) == 1
            assert note_mode_ids == [ANKING_MODEL_ID]


def test_note_type_contains_field(anki_session: AnkiSession) -> None:
    from ankihub.utils import note_type_contains_field

    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck):
            note_type = anki_session.mw.col.models.get(ANKING_MODEL_ID)
            assert note_type_contains_field(note_type, ANKING_MODEL_ID) is False
            note_type["flds"].append({"name": constants.ANKIHUB_NOTE_TYPE_FIELD_NAME})
            assert note_type_contains_field(note_type, ANKING_MODEL_ID) is True


def test_modify_note_type(anki_session: AnkiSession) -> None:
    from ankihub.register_decks import modify_note_type

    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck):
            note_type = anki_session.mw.col.models.get(ANKING_MODEL_ID)
            original_note_type = copy.deepcopy(note_type)
            original_note_template = original_note_type["tmpls"].pop()["afmt"]
            modify_note_type(note_type)
            modified_template = note_type["tmpls"].pop()["afmt"]
            # TODO Make more precise assertions.
            assert original_note_template != modified_template
            assert "AnkiHub ID" in modified_template


def test_prepare_to_upload_deck(anki_session: AnkiSession, monkeypatch):
    from ankihub.register_decks import create_shared_deck

    monkeypatch.setattr("ankihub.register_decks.askUser", Mock(return_value=True))
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            create_shared_deck(deck_id)


def test_populate_id_fields(anki_session: AnkiSession):
    from ankihub.register_decks import modify_note_type, populate_ankihub_id_fields

    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            note_type = anki_session.mw.col.models.get(ANKING_MODEL_ID)
            modify_note_type(note_type)
            populate_ankihub_id_fields(deck_id)
            # TODO add assertions once populate_ankihub_id_fields is complete.


def test_upload_deck(anki_session_with_config: AnkiSession, monkeypatch):
    from ankihub.register_decks import upload_deck

    anki_session = anki_session_with_config
    monkeypatch.setattr("ankihub.ankihub_client.requests", Mock())
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            upload_deck(deck_id)
