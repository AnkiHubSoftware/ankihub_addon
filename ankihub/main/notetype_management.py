from typing import List

from anki.models import NotetypeDict


# TODO
def add_notetype(notetype: NotetypeDict) -> None:
    print("add_notetype", notetype["name"])


def update_notetype_fields(notetype: NotetypeDict, fields: List[str]) -> None:
    print("update_notetype_fields", notetype["name"], fields)
