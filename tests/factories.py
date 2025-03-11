import datetime
import os
import uuid
from datetime import timezone
from typing import Generic, List, TypeVar

import factory
from faker import Faker

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import Deck, Field, NoteInfo
from ankihub.ankihub_client.models import (
    DeckExtension,
    DeckMedia,
    UserDeckExtensionRelation,
    UserDeckRelation,
)

fake = Faker()

T = TypeVar("T")


# Workaround to get type hints for factory.create(),
# see https://github.com/FactoryBoy/factory_boy/issues/468#issuecomment-759452373
class BaseFactory(factory.Factory, Generic[T]):
    @classmethod
    def create(cls, **kwargs) -> T:
        return super().create(**kwargs)


def _next_anki_nid() -> int:
    # Returns a new nid for each call.
    # The purpose of this is to make sure that the nids used by the NoteInfoFactory are unique.
    _next_anki_nid.nid += 1  # type: ignore
    return _next_anki_nid.nid  # type: ignore


_next_anki_nid.nid = 0  # type: ignore


class NoteInfoFactory(BaseFactory[NoteInfo]):
    class Meta:
        model = NoteInfo

    ah_nid: uuid.UUID = factory.LazyFunction(uuid.uuid4)  # type: ignore
    anki_nid: int = factory.LazyFunction(_next_anki_nid)  # type: ignore
    mid = 1
    fields: List[Field] = factory.LazyAttribute(  # type: ignore
        lambda _: [
            Field(name="Front", value="front"),
            Field(name="Back", value="back"),
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
    has_note_embeddings = False


class DeckMediaFactory(BaseFactory[DeckMedia]):
    class Meta:
        model = DeckMedia

    name: str = factory.LazyFunction(lambda: uuid.uuid4().hex)  # type: ignore
    file_content_hash: str = "test hash"
    modified: datetime.datetime = factory.LazyFunction(  # type: ignore
        lambda: datetime.datetime.now(tz=timezone.utc)
    )
    referenced_on_accepted_note: bool = False
    exists_on_s3: bool = False
    download_enabled: bool = True


class DeckExtensionFactory(BaseFactory[DeckExtension]):
    class Meta:
        model = DeckExtension

    id: int = factory.Sequence(lambda n: n)  # type: ignore
    ah_did: uuid.UUID
    owner_id: int = factory.LazyAttribute(lambda _: fake.random_int())  # type: ignore
    name: str = factory.LazyAttribute(lambda _: fake.word())  # type: ignore
    tag_group_name: str = factory.LazyAttribute(lambda _: fake.word())  # type: ignore
    description: str = factory.LazyAttribute(lambda _: fake.sentence())  # type: ignore
    user_relation: UserDeckExtensionRelation = UserDeckExtensionRelation.SUBSCRIBER
