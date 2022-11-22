import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, List, Optional

from anki.models import NotetypeId
from anki.notes import NoteId
from anki.utils import ids2str, split_fields
from aqt import mw

from . import LOGGER
from .settings import DB_PATH


def attach_ankihub_db_to_anki_db_connection() -> None:
    if AnkiHubDB.database_name not in [
        name for _, name, _ in mw.col.db.all("PRAGMA database_list")
    ]:
        mw.col.db.execute(
            f"ATTACH DATABASE ? AS {AnkiHubDB.database_name}", str(DB_PATH)
        )
        LOGGER.debug("Attached AnkiHub DB to Anki DB connection")


def detach_ankihub_db_from_anki_db_connection() -> None:
    if AnkiHubDB.database_name in [
        name for _, name, _ in mw.col.db.all("PRAGMA database_list")
    ]:
        # Liberal use of try/except to ensure we always try to detach and begin a new
        # transaction.
        try:
            # close the current transaction to avoid a "database is locked" error
            mw.col.save(trx=False)
        except Exception:
            LOGGER.debug("Failed to close transaction.")

        try:
            mw.col.db.execute(f"DETACH DATABASE {AnkiHubDB.database_name}")
            LOGGER.debug("Detached AnkiHub DB from Anki DB connection")
        except Exception:
            LOGGER.debug("Failed to detach AnkiHub database.")

        # begin a new transaction because Anki expects one to be open
        mw.col.db.begin()

        LOGGER.debug("Began new transaction.")


@contextmanager
def attached_ankihub_db():
    attach_ankihub_db_to_anki_db_connection()
    try:
        yield
    finally:
        detach_ankihub_db_from_anki_db_connection()


@contextmanager
def db_transaction():
    with sqlite3.connect(DB_PATH) as conn:
        yield conn
    conn.close()


class AnkiHubDB:

    # name of the database when attached to the Anki DB connection
    database_name = "ankihub_db"

    def __init__(self):
        self._setup_tables_and_migrate()

    def execute(self, sql: str, *args, first_row_only=False) -> List:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(sql, args)
        if first_row_only:
            result = c.fetchone()
        else:
            result = c.fetchall()
        c.close()
        conn.commit()
        conn.close()
        return result

    def scalar(self, sql: str, *args) -> Any:
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return rows[0]
        else:
            return None

    def list(self, sql: str, *args) -> List:
        return [x[0] for x in self.execute(sql, *args, first_row_only=False)]

    def _setup_tables_and_migrate(self) -> None:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                ankihub_note_id STRING PRIMARY KEY,
                ankihub_deck_id STRING,
                anki_note_id INTEGER,
                anki_note_type_id INTEGER
            )
            """
        )

        LOGGER.debug(f"AnkiHub DB schema version: {self.schema_version()}")

        if self.schema_version() == 0:
            self.execute(
                """
                ALTER TABLE notes ADD COLUMN mod INTEGER
                """
            )
            self.execute("PRAGMA user_version = 1;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

        if self.schema_version() <= 1:
            self.execute("CREATE INDEX ankihub_deck_id_idx ON notes (ankihub_deck_id)")
            self.execute("CREATE INDEX anki_note_id_idx ON notes (anki_note_id)")
            self.execute("PRAGMA user_version = 2;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

    def schema_version(self) -> int:
        result = self.scalar("PRAGMA user_version;")
        return result

    def save_notes_from_nids(self, ankihub_did: str, nids: List[NoteId]):
        with db_transaction() as conn:
            raw_notes = mw.col.db.all(
                f"SELECT id, mid, mod, flds FROM notes WHERE id IN {ids2str(nids)}"
            )
            for raw_note in raw_notes:
                nid, mid, mod, flds = raw_note
                ankihub_id = split_fields(flds)[-1]
                conn.execute(
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
                        ankihub_id,
                        ankihub_did,
                        nid,
                        mid,
                        mod,
                    ),
                )

    def notes_for_ankihub_deck(self, ankihub_did: str) -> List[NoteId]:
        result = self.list(
            """
            SELECT anki_note_id FROM notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )
        return result

    def ankihub_did_for_note_type(self, anki_note_type_id: int) -> Optional[str]:
        result = self.scalar(
            """
            SELECT ankihub_deck_id FROM notes WHERE anki_note_type_id = ?
            """,
            anki_note_type_id,
        )
        return result

    def ankihub_id_for_note(self, anki_note_id: int) -> Optional[str]:
        result = self.scalar(
            """
            SELECT ankihub_note_id FROM notes WHERE anki_note_id = ?
            """,
            anki_note_id,
        )
        return result

    def note_types_for_ankihub_deck(self, ankihub_did: str) -> List[NotetypeId]:
        result = self.list(
            """
            SELECT DISTINCT anki_note_type_id FROM notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )
        return result

    def remove_deck(self, ankihub_did: str):
        self.execute(
            """
            DELETE FROM notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )

    def ankihub_deck_ids(self) -> List[str]:
        result = self.list("SELECT DISTINCT ankihub_deck_id FROM notes")
        return result

    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        result = self.scalar(
            "SELECT mod FROM notes WHERE ankihub_note_id = ?",
            str(ankihub_note_id),
        )
        return result


ankihub_db = AnkiHubDB()
