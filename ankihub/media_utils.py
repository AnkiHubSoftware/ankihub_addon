from typing import Dict, List, Sequence, Set

import aqt

from .ankihub_client import NoteInfo
from .common_utils import local_image_names_from_html


def get_img_names_from_notes(
    notes: Sequence[NoteInfo], asset_disabled_fields: Dict[int, List[str]] = {}
) -> Set[str]:
    """Return the names of all images on the given notes.
    Does only return names of local images, not remote images."""
    imgs: Set[str] = set()
    for note in notes:
        disabled_fields = asset_disabled_fields.get(note.mid, [])
        for field in note.fields:
            if field.name in disabled_fields:
                continue
            imgs = imgs.union(local_image_names_from_html(field.value))
    return imgs


def find_and_replace_text_in_fields_on_all_notes(old: str, new: str) -> None:
    # Used to rename images across all notes in the collection.

    aqt.mw.col.db.execute(
        "UPDATE notes SET flds = REPLACE(flds, ?, ?)",
        old,
        new,
    )
