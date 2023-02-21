import os
import uuid
from typing import List

import factory

# workaround for vscode test discovery not using pytest.ini which sets this env var
# has to be set before importing ankihub
os.environ["SKIP_INIT"] = "1"

from ankihub.ankihub_client import Field, NoteInfo


class NoteInfoFactory(factory.Factory):
    class Meta:
        model = NoteInfo

    ankihub_note_uuid = factory.LazyFunction(uuid.uuid4)
    anki_nid = 1
    mid = 1
    fields = [
        Field(name="Front", value="front", order=0),
        Field(name="Back", value="back", order=1),
    ]
    tags: List[str] = []
    guid = "old guid"

    def __new__(self, *args, **kwargs) -> NoteInfo:
        return super().__new__(*args, **kwargs)
