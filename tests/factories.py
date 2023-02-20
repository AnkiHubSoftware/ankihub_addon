import uuid
from typing import List

import factory

from ankihub.ankihub_client import Field, NoteInfo


class NoteInfoFactory(factory.Factory):
    class Meta:
        model = NoteInfo

    ankihub_note_uuid = factory.LazyFunction(uuid.uuid4)
    anki_nid = 1
    mid = 1
    fields = [Field(name="Front", value="front", order=0)]
    tags: List[str] = []
    guid = "old guid"
