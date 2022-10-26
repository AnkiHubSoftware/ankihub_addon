import uuid
from functools import wraps
from typing import Callable, List, Optional

from anki.dbproxy import DBProxy
from anki.models import NotetypeId
from anki.notes import NoteId
from aqt import mw
from aqt.gui_hooks import collection_did_load, collection_did_temporarily_close

from . import LOGGER
from .settings import ANKIHUB_NOTE_TYPE_FIELD_NAME, DB_PATH


def attach_ankihub_db(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        attach_ankihub_db_to_anki_db_connection()
        return func(*args, **kwargs)

    return wrapper


def attach_ankihub_db_to_anki_db_connection() -> None:
    if "ankihub_db" not in [
        name for _, name, _ in mw.col.db.all("PRAGMA database_list")
    ]:
        mw.col.db.execute(
            f"ATTACH DATABASE ? AS {AnkiHubDB.database_name}", str(DB_PATH)
        )
        LOGGER.debug("Attached AnkiHub DB to Anki DB connection")
    else:
        LOGGER.debug("AnkiHub DB already attached to Anki DB connection")


class AnkiHubDB:
    database_name = "ankihub_db"

    @property
    def anki_db(self) -> DBProxy:
        return mw.col.db

    def setup(self):
        self._setup_tables_and_migrate()
        self.anki_db.commit()

    @attach_ankihub_db
    def _setup_tables_and_migrate(self) -> None:
        self.anki_db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.database_name}.notes (
                ankihub_note_id STRING PRIMARY KEY,
                ankihub_deck_id STRING,
                anki_note_id INTEGER,
                anki_note_type_id INTEGER
            )
            """
        )

        LOGGER.debug(f"AnkiHub DB schema version: {self.schema_version()}")

        if self.schema_version() == 0:
            self.anki_db.execute(
                f"""
                ALTER TABLE {self.database_name}.notes ADD COLUMN mod INTEGER
                """
            )
            self.anki_db.execute(f"PRAGMA {self.database_name}.user_version = 1;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

        if self.schema_version() <= 1:
            self.anki_db.execute(
                f"CREATE INDEX {self.database_name}.ankihub_deck_id_idx ON notes (ankihub_deck_id)"
            )
            self.anki_db.execute(
                f"CREATE INDEX {self.database_name}.anki_note_id_idx ON notes (anki_note_id)"
            )
            self.anki_db.execute(f"PRAGMA {self.database_name}.user_version = 2;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

    @attach_ankihub_db
    def schema_version(self) -> int:
        result = self.anki_db.scalar(f"PRAGMA {self.database_name}.user_version;")
        return result

    @attach_ankihub_db
    def save_notes_from_nids(self, ankihub_did: str, nids: List[NoteId]):
        for nid in nids:
            note = mw.col.get_note(nid)
            self.anki_db.execute(
                f"""
                INSERT OR REPLACE INTO {self.database_name}.notes (
                    ankihub_note_id,
                    ankihub_deck_id,
                    anki_note_id,
                    anki_note_type_id,
                    mod
                ) VALUES (?, ?, ?, ?, ?)
                """,
                note[ANKIHUB_NOTE_TYPE_FIELD_NAME],
                ankihub_did,
                nid,
                note.mid,
                note.mod,
            )

    @attach_ankihub_db
    def notes_for_ankihub_deck(self, ankihub_did: str) -> List[NoteId]:
        result = self.anki_db.list(
            f"""
            SELECT anki_note_id FROM {self.database_name}.notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )
        return result

    @attach_ankihub_db
    def ankihub_did_for_note_type(self, anki_note_type_id: int) -> Optional[str]:
        result = self.anki_db.scalar(
            f"""
            SELECT ankihub_deck_id FROM {self.database_name}.notes WHERE anki_note_type_id = ?
            """,
            anki_note_type_id,
        )
        return result

    @attach_ankihub_db
    def ankihub_id_for_note(self, anki_note_id: int) -> Optional[str]:
        result = self.anki_db.scalar(
            f"""
            SELECT ankihub_note_id FROM {self.database_name}.notes WHERE anki_note_id = ?
            """,
            anki_note_id,
        )
        return result

    @attach_ankihub_db
    def note_types_for_ankihub_deck(self, ankihub_did: str) -> List[NotetypeId]:
        result = self.anki_db.list(
            f"""
            SELECT DISTINCT anki_note_type_id FROM {self.database_name}.notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )
        return result

    @attach_ankihub_db
    def remove_deck(self, ankihub_did: str):
        self.anki_db.execute(
            f"""
            DELETE FROM {self.database_name}.notes WHERE ankihub_deck_id = ?
            """,
            ankihub_did,
        )

    @attach_ankihub_db
    def ankihub_deck_ids(self) -> List[str]:
        result = self.anki_db.list(
            f"SELECT DISTINCT ankihub_deck_id FROM {self.database_name}.notes"
        )
        return result

    @attach_ankihub_db
    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        result = self.anki_db.scalar(
            f"SELECT mod FROM {self.database_name}.notes WHERE ankihub_note_id = ?",
            str(ankihub_note_id),
        )
        return result


ankihub_db = AnkiHubDB()


def setup_ankihub_database():
    """Sets up the AnkiHub database and attaches it to the Anki database connection
    when Anki opens its database.
    """
    collection_did_load.append(lambda _: ankihub_db.setup())
    collection_did_temporarily_close.append(lambda _: ankihub_db.setup())
