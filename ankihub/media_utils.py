import re
from typing import Sequence, Set

import aqt
from anki.notes import NoteId
from anki.utils import ids2str


def get_img_names_from_notes(nids: Sequence[NoteId]) -> Set[str]:
    flds_with_imgs = aqt.mw.col.db.list(
        f"SELECT flds FROM notes WHERE id IN {ids2str(nids)} AND flds LIKE '%<img%'",
    )

    imgs = set()
    # TODO: check if we should use a different regex.
    img_re = re.compile(r'<img.*?src="(.*?)"')
    for flds in flds_with_imgs:
        for match in re.finditer(img_re, flds):
            img = match.group(1)
            # TODO Maybe move the prefix check to the regex for better performance.
            if not any([http_prefix in img for http_prefix in ["http://", "https://"]]):
                imgs.add(img)
            imgs.add(img)

    return imgs


def find_and_replace_text_in_fields(old: str, new: str) -> None:
    # TODO This not used anywhere yet.
    # Could be used to rename images across all notes in the collection.
    # Maybe we should use aqt.mw.col.find_and_replace() instead?
    aqt.mw.col.db.execute(
        "UPDATE notes SET flds = REPLACE(flds, ?, ?)",
        old,
        new,
    )
