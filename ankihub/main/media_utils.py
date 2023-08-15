"""Performant functions for working with media in Anki"""
import aqt


def find_and_replace_text_in_fields_on_all_notes(old: str, new: str) -> None:
    # Used to rename media across all notes in the collection.

    aqt.mw.col.db.execute(
        "UPDATE notes SET flds = REPLACE(flds, ?, ?)",
        old,
        new,
    )
    aqt.mw.col.save()
