import sqlite3
from typing import List

from anki.models import NotetypeId
from anki.notes import NoteId
from aqt import mw

from .constants import ANKIHUB_NOTE_TYPE_FIELD_NAME, DB_PATH


class AnkiHubDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.c = self.conn.cursor()
        self.c.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                ankihub_note_id STRING PRIMARY KEY,
                ankihub_deck_id STRING,
                anki_note_id INTEGER,
                anki_note_type_id INTEGER
            )
            """
        )
        self.migrate()

    def migrate(self) -> None:
        if self.schema_version() == 0:
            self.c.execute(
                """
                ALTER TABLE notes ADD COLUMN mod INTEGER
                """
            )
            self.c.execute("PRAGMA user_version = 1;")
            self.conn.commit()

    def schema_version(self) -> int:
        self.c.execute("PRAGMA user_version;")
        result = self.c.fetchone()[0]
        return result

    def save_notes_from_nids(self, ankihub_did: str, nids: List[NoteId]):
        for nid in nids:
            note = mw.col.get_note(nid)
            self.c.execute(
                """
                INSERT OR REPLACE INTO notes (
                    ankihub_note_id,
                    ankihub_deck_id,
                    anki_note_id,
                    anki_note_type_id,
                    mod
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    note[ANKIHUB_NOTE_TYPE_FIELD_NAME],
                    ankihub_did,
                    nid,
                    note.mid,
                    note.mod,
                ),
            )

        self.conn.commit()

    def notes_for_ankihub_deck(self, ankihub_did: str) -> List[NoteId]:
        self.c.execute(
            """
            SELECT anki_note_id FROM notes WHERE ankihub_deck_id = ?
            """,
            (ankihub_did,),
        )
        result = [NoteId(x[0]) for x in self.c.fetchall()]
        return result

    def ankihub_did_for_note(self, anki_note_id: int) -> str:
        self.c.execute(
            """
            SELECT ankihub_deck_id FROM notes WHERE anki_note_id = ?
            """,
            (anki_note_id,),
        )
        return self.c.fetchone()[0]

    def ankihub_id_for_note(self, anki_note_id: int) -> str:
        self.c.execute(
            """
            SELECT ankihub_note_id FROM notes WHERE anki_note_id = ?
            """,
            (anki_note_id,),
        )
        return self.c.fetchone()[0]

    def note_types_for_ankihub_deck(self, ankihub_did: str) -> List[NotetypeId]:
        self.c.execute(
            """
            SELECT DISTINCT anki_note_type_id FROM notes WHERE ankihub_deck_id = ?
            """,
            (ankihub_did,),
        )
        result = [NotetypeId(x[0]) for x in self.c.fetchall()]
        return result

    def remove_deck(self, ankihub_did: str):
        self.c.execute(
            """
            DELETE FROM notes WHERE ankihub_deck_id = ?
            """,
            (ankihub_did,),
        )
        self.conn.commit()

    def ankihub_deck_ids(self) -> List[str]:
        self.c.execute("SELECT DISTINCT ankihub_deck_id FROM notes")
        return [x[0] for x in self.c.fetchall()]
