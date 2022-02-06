from pytest_anki import AnkiSession


def test_entry_point(anki_session: AnkiSession):
    from anki_dev.qt.aqt.main import AnkiQt

    from src.ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)
