import pytest

from aqt.main import AnkiQt

from pytest_anki import profile_loaded, AnkiSession


@pytest.fixture(scope="session")
def mw(anki_session: AnkiSession):
    with profile_loaded(anki_session.mw):
        yield anki_session.mw


@pytest.fixture(scope="session")
def col(mw: AnkiQt):
    yield mw.col
