from pytest_anki import AnkiSession


def test_entry_point(anki_session: AnkiSession):
    from aqt.main import AnkiQt

    from ankihub import entry_point

    mw = entry_point.run()
    assert isinstance(mw, AnkiQt)
