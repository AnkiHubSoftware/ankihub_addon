import copy
import pathlib

from ankihub_addon.src.ankihub import consts
from pytest_anki import AnkiSession

ANKING_MODEL_ID = 1566160514431

anking_deck = str(pathlib.Path(__file__).parent / "test_data" / "anking.apkg")


def test_get_note_types_in_deck(anki_session: AnkiSession) -> None:
    """Check that get_note_types_in_deck returns the expected model id."""
    from ankihub.sync import get_note_types_in_deck
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            note_mode_ids = get_note_types_in_deck(deck_id)
            # TODO test on a deck that has more than one note type.
            assert len(note_mode_ids) == 1
            assert note_mode_ids == [ANKING_MODEL_ID]


def test_note_type_contains_field(anki_session: AnkiSession) -> None:
    from ankihub_addon.src.ankihub.utils import note_type_contains_field
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck):
            note_type = anki_session.mw.col.models.get(ANKING_MODEL_ID)
            assert note_type_contains_field(note_type, ANKING_MODEL_ID) is False
            note_type["flds"].append({"name": consts.ANKIHUB_NOTE_TYPE_FIELD_NAME})
            assert note_type_contains_field(note_type, ANKING_MODEL_ID) is True


def test_modify_note_type(anki_session: AnkiSession) -> None:
    from ankihub_addon.src.ankihub.sync import modify_note_type
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck):
            previous_note_template = copy.deepcopy(note_type["tmpls"])
            modify_note_type(note_type)
            note_type = anki_session.mw.col.models.get(ANKING_MODEL_ID)
            assert note_type_contains_field(note_type, ANKING_MODEL_ID)
            modified_template = note_type["tmpls"]
            assert len(previous_note_template) == len(modified_template)
            # TODO Make an assertion about the actual diff
            assert previous_note_template != modified_template


def test_prepare_to_upload_deck(anki_session: AnkiSession):
    from ankihub.sync import prepare_to_upload_deck
    with anki_session.profile_loaded():
        with anki_session.deck_installed(anking_deck) as deck_id:
            prepare_to_upload_deck(deck_id)


def test_add_id_fields():
    pass
