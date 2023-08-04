import datetime
import os
import uuid
from datetime import timezone
from typing import Generic, List, TypeVar

import factory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import Deck, Field, NoteInfo
from ankihub.ankihub_client.models import UserDeckRelation

T = TypeVar("T")


# Workaround to get type hints for factory.create(),
# see https://github.com/FactoryBoy/factory_boy/issues/468#issuecomment-759452373
class BaseFactory(factory.Factory, Generic[T]):
    @classmethod
    def create(cls, **kwargs) -> T:
        return super().create(**kwargs)


class NoteInfoFactory(BaseFactory[NoteInfo]):
    class Meta:
        model = NoteInfo

    ah_nid = factory.LazyFunction(uuid.uuid4)
    anki_nid = 1
    mid = 1
    fields: List[Field] = factory.LazyAttribute(  # type: ignore
        lambda _: [
            Field(name="Front", value="front", order=0),
            Field(name="Back", value="back", order=1),
        ]
    )
    tags: List[str] = []
    guid = "old guid"


class DeckFactory(BaseFactory[Deck]):
    class Meta:
        model = Deck

    ah_did = uuid.uuid4()
    name = "Test Deck"
    anki_did = 1
    csv_last_upload = datetime.datetime.now(tz=timezone.utc)
    csv_notes_filename = "test.csv"
    media_upload_finished = False
    user_relation = UserDeckRelation.SUBSCRIBER
