import re
from typing import Sequence, Set

import aqt
from anki.notes import NoteId
from anki.utils import ids2str

from .common_utils import IMG_NAME_IN_IMG_TAG_REGEX


def get_img_names_from_notes(nids: Sequence[NoteId]) -> Set[str]:
    """Return the names of all images on the given notes.
    Does only return names of local images, not remote images."""

    flds_with_imgs = aqt.mw.col.db.list(
        f"SELECT flds FROM notes WHERE id IN {ids2str(nids)} AND flds LIKE '%<img%'",
    )

    imgs = set()
    for flds in flds_with_imgs:
        for img in re.findall(IMG_NAME_IN_IMG_TAG_REGEX, flds):
            if img.startswith("http://") or img.startswith("https://"):
                continue
            imgs.add(img)

    return imgs


def find_and_replace_text_in_fields(old: str, new: str) -> None:
    # TODO This is not used anywhere yet.
    # Could be used to rename images across all notes in the collection.
    # Maybe we should use aqt.mw.col.find_and_replace() instead?
    aqt.mw.col.db.execute(
        "UPDATE notes SET flds = REPLACE(flds, ?, ?)",
        old,
        new,
    )
