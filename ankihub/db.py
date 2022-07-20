import sqlite3
from pathlib import Path
from typing import Dict, List

from anki.notes import NoteId

DB_PATH = Path(__file__).parent / "user_files/ankihub.db"


class AnkiHubDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.c = self.conn.cursor()
        self.c.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                ankihub_note_id STRING PRIMARY KEY,
                ankihub_deck_id STRING,
                anki_note_id INTEGER
            )
            """
        )

    def save_notes(self, ankihub_did: str, notes_data: List[Dict]):
        for note_data in notes_data:
            self.c.execute(
                """
                INSERT OR REPLACE INTO notes (
                    ankihub_note_id,
                    ankihub_deck_id,
                    anki_note_id
                ) VALUES (?, ?, ?)
                """,
                (
                    note_data["ankihub_id"],
                    ankihub_did,
                    note_data["anki_id"],
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
