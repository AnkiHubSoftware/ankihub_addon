from typing import Generator

import pytest

from aqt.main import AnkiQt
from anki.collection import Collection

from pytest_anki import profile_loaded, AnkiSession, anki_running


@pytest.fixture(scope="session")
def anki_session(request: pytest.FixtureRequest) -> Generator[AnkiSession, None, None]:
    param = getattr(request, "param", None)
    with anki_running() if not param else anki_running(**param) as session:
        yield session


@pytest.fixture(scope="session")
def mw(anki_session: AnkiSession) -> Generator[AnkiQt, None, None]:
    with profile_loaded(anki_session.mw):
        yield anki_session.mw


@pytest.fixture(scope="session")
def col(mw: AnkiQt) -> Generator[Collection, None, None]:
    yield mw.col
