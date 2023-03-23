from typing import List

from .. import LOGGER
from .db import ankihub_db


def migrate_ankihub_db():
    LOGGER.info(f"AnkiHub DB schema version: {ankihub_db.schema_version()}")

    if ankihub_db.schema_version() == 0:
        with ankihub_db.connection() as conn:
            conn.execute(
                """
                ALTER TABLE notes ADD COLUMN mod INTEGER
                """
            )
            conn.execute("PRAGMA user_version = 1;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 1:
        with ankihub_db.connection() as conn:
            conn.execute("CREATE INDEX ankihub_deck_id_idx ON notes (ankihub_deck_id)")
            conn.execute("CREATE INDEX anki_note_id_idx ON notes (anki_note_id)")
            conn.execute("PRAGMA user_version = 2;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 2:
        with ankihub_db.connection() as conn:
            conn.execute("ALTER TABLE notes ADD COLUMN guid TEXT")
            conn.execute("ALTER TABLE notes ADD COLUMN fields TEXT")
            conn.execute("ALTER TABLE notes ADD COLUMN tags TEXT")
            conn.execute("PRAGMA user_version = 3;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 3:
        with ankihub_db.connection() as conn:
            conn.execute("ALTER TABLE notes ADD COLUMN last_update_type TEXT")
            conn.execute("PRAGMA user_version = 4;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 4:
        with ankihub_db.connection() as conn:
            conn.execute("CREATE INDEX anki_note_type_id ON notes (anki_note_type_id)")
            conn.execute("PRAGMA user_version = 5;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 5:

        with ankihub_db.connection() as conn:
            # find conflicting notes - notes that have the same anki_note_id
            anki_nids_with_conflicts: List[str] = ankihub_db.list(
                "SELECT anki_note_id FROM notes GROUP BY anki_note_id HAVING COUNT(*) > 1"
            )

            for anki_nid in anki_nids_with_conflicts:
                # get the note that has the highest mod value
                # this is the one that is also in the anki database because it was synced last
                ah_nid_with_highest_mod = ankihub_db.scalar(
                    "SELECT ankihub_note_id FROM notes WHERE anki_note_id = ? ORDER BY mod DESC LIMIT 1",
                    anki_nid,
                )

                # delete all notes with the same anki_note_id except the one with the highest mod value
                conn.execute(
                    "DELETE FROM notes WHERE anki_note_id = ? AND ankihub_note_id != ?",
                    anki_nid,
                    ah_nid_with_highest_mod,
                )

            # Add an unique constraint to the anki_note_id column by making an unique index.
            # You can't add a unique constraint to an existing table in sqlite and
            # this is equlivalent, see https://www.sqlite.org/lang_createtable.html#constraints
            conn.execute("DROP INDEX anki_note_id_idx")
            conn.execute("CREATE UNIQUE INDEX anki_note_id_idx ON notes (anki_note_id)")

            # rename anki_note_type_id index to anki_note_type_id_idx to be consistent with other indexes
            conn.execute("DROP INDEX anki_note_type_id")
            conn.execute(
                "CREATE INDEX anki_note_type_id_idx ON notes (anki_note_type_id)"
            )

            conn.execute("PRAGMA user_version = 6;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )
