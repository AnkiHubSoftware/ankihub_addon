from . import LOGGER
from .db import ankihub_db


def migrate_ankihub_db():
    LOGGER.info(f"AnkiHub DB schema version: {ankihub_db.schema_version()}")

    if ankihub_db.schema_version() == 0:
        ankihub_db.execute(
            """
            ALTER TABLE notes ADD COLUMN mod INTEGER
            """
        )
        ankihub_db.execute("PRAGMA user_version = 1;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 1:
        ankihub_db.execute(
            "CREATE INDEX ankihub_deck_id_idx ON notes (ankihub_deck_id)"
        )
        ankihub_db.execute("CREATE INDEX anki_note_id_idx ON notes (anki_note_id)")
        ankihub_db.execute("PRAGMA user_version = 2;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 2:
        ankihub_db.execute("ALTER TABLE notes ADD COLUMN guid TEXT")
        ankihub_db.execute("ALTER TABLE notes ADD COLUMN fields TEXT")
        ankihub_db.execute("ALTER TABLE notes ADD COLUMN tags TEXT")
        ankihub_db.execute("PRAGMA user_version = 3;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 3:
        ankihub_db.execute("ALTER TABLE notes ADD COLUMN last_update_type TEXT")
        ankihub_db.execute("PRAGMA user_version = 4;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() <= 4:
        ankihub_db.execute(
            "CREATE INDEX anki_note_type_id ON notes (anki_note_type_id)"
        )
        ankihub_db.execute("PRAGMA user_version = 5;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )
