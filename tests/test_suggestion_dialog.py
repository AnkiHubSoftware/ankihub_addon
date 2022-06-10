from pytest_anki import AnkiSession


def test_all_change_types_have_display_names(anki_session_with_addon: AnkiSession):
    from ankihub.constants import ChangeTypes
    from ankihub.gui.suggestion_dialog import DISPLAY_NAME_TO_CHANGE_TYPE

    assert set(DISPLAY_NAME_TO_CHANGE_TYPE.values()) == set(ChangeTypes)
