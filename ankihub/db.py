import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterable, List, Optional, Tuple

from anki.models import NotetypeId
from anki.notes import NoteId
from anki.utils import ids2str, join_fields, split_fields
from aqt import mw

from . import LOGGER
from .ankihub_client import Field, NoteInfo
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

    def first(self, sql: str, *args) -> Optional[Tuple]:
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return tuple(rows)
        else:
            return None

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

        if self.schema_version() <= 2:
            self.execute("ALTER TABLE notes ADD COLUMN guid TEXT")
            self.execute("ALTER TABLE notes ADD COLUMN fields TEXT")
            self.execute("ALTER TABLE notes ADD COLUMN tags TEXT")
            self.execute("PRAGMA user_version = 3;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

    def schema_version(self) -> int:
        result = self.scalar("PRAGMA user_version;")
        return result

    def save_notes_data_and_mod_values(
        self, ankihub_did: str, notes_data: List[NoteInfo]
    ):
        """Save notes data in the AnkiHub DB.
        It also takes mod values for the notes from the Anki DB and saves them to the AnkiHub DB.
        This is why it should be be called after importing or exporting a deck.
        (The mod values are used to determine if a note has been modified in Anki since it was last imported/exported.)
        """
        with db_transaction() as conn:
            for note_data in notes_data:
                mod = mw.col.db.scalar(
                    f"SELECT mod FROM notes WHERE id = {note_data.anki_nid}",
                )
                fields = join_fields(
                    [
                        field.value
                        for field in sorted(
                            note_data.fields, key=lambda field: field.order
                        )
                    ]
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO notes (
                        ankihub_note_id,
                        ankihub_deck_id,
                        anki_note_id,
                        anki_note_type_id,
                        mod,
                        fields,
                        tags,
                        guid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(note_data.ankihub_note_uuid),
                        ankihub_did,
                        note_data.anki_nid,
                        note_data.mid,
                        mod,
                        fields,
                        mw.col.tags.join(note_data.tags),
                        note_data.guid,
                    ),
                )

    def reset_mod_values_in_anki_db(self, anki_nids: List[int]) -> None:
        # resets the mod values of the notes in the Anki DB to the
        # mod values stored in the AnkiHub DB
        nid_mod_tuples = self.execute(
            f"""
            SELECT anki_note_id, mod from notes WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        for nid, mod in nid_mod_tuples:
            mw.col.db.execute(
                "UPDATE notes SET mod = ? WHERE id = ?",
                mod,
                nid,
            )

    def note_data(self, anki_note_id: int) -> Optional[NoteInfo]:
        result = self.first(
            f"""
            SELECT
                ankihub_note_id,
                anki_note_id,
                anki_note_type_id,
                tags,
                fields,
                guid
            FROM notes
            WHERE anki_note_id = {anki_note_id}
            """,
        )
        if result is None:
            return None

        ah_nid, anki_nid, mid, tags, flds, guid = result
        field_names = [
            field["name"] for field in mw.col.models.get(NotetypeId(mid))["flds"]
        ]
        return NoteInfo(
            ankihub_note_uuid=uuid.UUID(ah_nid),
            anki_nid=anki_nid,
            mid=mid,
            tags=mw.col.tags.split(tags),
            fields=[
                Field(
                    name=field_names[i],
                    value=value,
                    order=i,
                )
                for i, value in enumerate(split_fields(flds))
            ],
            guid=guid,
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

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        result = self.list(
            f"""
            SELECT DISTINCT ankihub_deck_id FROM notes WHERE anki_note_id IN {ids2str(anki_nids)}
            """
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

    def ankihub_dids_of_decks_with_missing_values(self) -> List[str]:
        # currently only checks the guid, fields and tags columns
        result = self.list(
            "SELECT DISTINCT ankihub_deck_id FROM notes WHERE "
            "guid IS NULL OR "
            "fields IS NULL OR "
            "tags IS NULL"
        )
        return result


ankihub_db = AnkiHubDB()
