from . import LOGGER
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
