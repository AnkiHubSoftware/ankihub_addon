import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from anki.models import NotetypeId
from anki.notes import NoteId
from anki.utils import ids2str, join_fields, split_fields
from aqt import mw

from . import LOGGER
from .ankihub_client import Field, NoteInfo, suggestion_type_from_str
from .settings import ankihub_db_path


def attach_ankihub_db_to_anki_db_connection() -> None:
    if AnkiHubDB.database_name not in [
        name for _, name, _ in mw.col.db.all("PRAGMA database_list")
    ]:
        mw.col.db.execute(
            f"ATTACH DATABASE ? AS {AnkiHubDB.database_name}",
            str(AnkiHubDB.database_path),
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
    with sqlite3.connect(AnkiHubDB.database_path) as conn:
        yield conn
    conn.close()


class AnkiHubDB:

    # name of the database when attached to the Anki DB connection
    database_name = "ankihub_db"
    database_path: Optional[Path] = None

    def setup_and_migrate(self) -> None:
        AnkiHubDB.database_path = ankihub_db_path()

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

        if self.schema_version() <= 3:
            self.execute("ALTER TABLE notes ADD COLUMN last_update_type TEXT")
            self.execute("PRAGMA user_version = 4;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

        if self.schema_version() <= 4:
            self.execute("CREATE INDEX anki_note_type_id ON notes (anki_note_type_id)")
            self.execute("PRAGMA user_version = 5;")
            LOGGER.debug(
                f"AnkiHub DB migrated to schema version {self.schema_version()}"
            )

    def execute(self, sql: str, *args, first_row_only=False) -> List:
        conn = sqlite3.connect(self.database_path)
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

    def schema_version(self) -> int:
        result = self.scalar("PRAGMA user_version;")
        return result

    def save_notes_data_and_mod_values(
        self, ankihub_did: uuid.UUID, notes_data: List[NoteInfo]
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
                        guid,
                        last_update_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(note_data.ankihub_note_uuid),
                        str(ankihub_did),
                        note_data.anki_nid,
                        note_data.mid,
                        mod,
                        fields,
                        mw.col.tags.join(note_data.tags),
                        note_data.guid,
                        note_data.last_update_type.value[0]
                        if note_data.last_update_type is not None
                        else None,
                    ),
                )

    def reset_mod_values_in_anki_db(self, anki_nids: List[NoteId]) -> None:
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

    def note_data(self, anki_note_id: NoteId) -> Optional[NoteInfo]:
        # The AnkiHub note type of the note has to exist in the Anki DB, otherwise this will fail.
        result = self.first(
            f"""
            SELECT
                ankihub_note_id,
                anki_note_id,
                anki_note_type_id,
                tags,
                fields,
                guid,
                last_update_type
            FROM notes
            WHERE anki_note_id = {anki_note_id}
            """,
        )
        if result is None:
            return None

        ah_nid, anki_nid, mid, tags, flds, guid, last_update_type = result
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
            last_update_type=suggestion_type_from_str(last_update_type)
            if last_update_type
            else None,
        )

    def anki_nids_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NoteId]:
        result = self.list(
            """
            SELECT anki_note_id FROM notes WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )
        return result

    def ankihub_dids(self) -> List[uuid.UUID]:
        result = [
            uuid.UUID(did)
            for did in self.list("SELECT DISTINCT ankihub_deck_id FROM notes")
        ]
        return result

    def ankihub_did_for_note_type(
        self, anki_note_type_id: NotetypeId
    ) -> Optional[uuid.UUID]:
        did_str = self.scalar(
            """
            SELECT ankihub_deck_id FROM notes WHERE anki_note_type_id = ?
            """,
            anki_note_type_id,
        )
        if did_str is None:
            return None

        result = uuid.UUID(did_str)
        return result

    def ankihub_did_for_anki_nid(self, anki_nid: NoteId) -> Optional[uuid.UUID]:
        did_str = self.scalar(
            f"""
            SELECT ankihub_deck_id FROM notes WHERE anki_note_id = {anki_nid}
            """
        )
        result = uuid.UUID(did_str)
        return result

    def ankihub_dids_for_anki_nids(
        self, anki_nids: Iterable[NoteId]
    ) -> List[uuid.UUID]:
        did_strs = self.list(
            f"""
            SELECT DISTINCT ankihub_deck_id FROM notes WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        result = [uuid.UUID(did) for did in did_strs]
        return result

    def are_ankihub_notes(self, anki_nids: List[NoteId]) -> bool:
        notes_count = self.scalar(
            f"""
            SELECT COUNT(*) FROM notes WHERE anki_note_id IN {ids2str(anki_nids)}
            """
        )
        return notes_count == len(set(anki_nids))

    def ankihub_nid_for_anki_nid(self, anki_note_id: NoteId) -> Optional[uuid.UUID]:
        nid_str = self.scalar(
            """
            SELECT ankihub_note_id FROM notes WHERE anki_note_id = ?
            """,
            anki_note_id,
        )
        if nid_str is None:
            return None

        result = uuid.UUID(nid_str)
        return result

    def anki_nid_for_ankihub_nid(self, ankihub_id: uuid.UUID) -> Optional[NoteId]:
        note_id_str = self.scalar(
            """
            SELECT anki_note_id FROM notes WHERE ankihub_note_id = ?
            """,
            ankihub_id,
        )
        if note_id_str is None:
            return None

        result = NoteId(note_id_str)
        return result

    def ankihub_note_type_ids(self) -> List[NotetypeId]:
        result = self.list("SELECT DISTINCT anki_note_type_id FROM notes")
        return result

    def is_ankihub_note_type(self, anki_note_type_id: NotetypeId) -> bool:
        result = self.scalar(
            """
            SELECT EXISTS(SELECT 1 FROM notes WHERE anki_note_type_id = ?)
            """,
            anki_note_type_id,
        )
        return result

    def note_types_for_ankihub_deck(self, ankihub_did: uuid.UUID) -> List[NotetypeId]:
        result = self.list(
            """
            SELECT DISTINCT anki_note_type_id FROM notes WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )
        return result

    def remove_deck(self, ankihub_did: uuid.UUID):
        self.execute(
            """
            DELETE FROM notes WHERE ankihub_deck_id = ?
            """,
            str(ankihub_did),
        )

    def ankihub_deck_ids(self) -> List[uuid.UUID]:
        result = [
            uuid.UUID(did)
            for did in self.list("SELECT DISTINCT ankihub_deck_id FROM notes")
        ]
        return result

    def last_sync(self, ankihub_note_id: uuid.UUID) -> Optional[int]:
        result = self.scalar(
            "SELECT mod FROM notes WHERE ankihub_note_id = ?",
            str(ankihub_note_id),
        )
        return result

    def ankihub_dids_of_decks_with_missing_values(self) -> List[uuid.UUID]:
        # currently only checks the guid, fields and tags columns
        did_strs = self.list(
            "SELECT DISTINCT ankihub_deck_id FROM notes WHERE "
            "guid IS NULL OR "
            "fields IS NULL OR "
            "tags IS NULL"
        )
        result = [uuid.UUID(did) for did in did_strs]
        return result


ankihub_db = AnkiHubDB()
