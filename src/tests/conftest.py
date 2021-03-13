from typing import Iterator

import pytest

from aqt.main import AnkiQt

from pytest_anki import profile_loaded, AnkiSession, anki_running


@pytest.fixture(scope="session")
def anki_session(request) -> Iterator[AnkiSession]:
    param = getattr(request, "param", None)
    with anki_running() if not param else anki_running(**param) as session:
        yield session


@pytest.fixture(scope="session")
def mw(anki_session: AnkiSession):
    with profile_loaded(anki_session.mw):
        yield anki_session.mw


@pytest.fixture(scope="session")
def col(mw: AnkiQt):
    yield mw.col
