from typing import List

from .. import LOGGER
from .db import ankihub_db
from .models import (
    AnkiHubNote,
    AnkiHubNoteType,
    BaseModel,
    DeckMedia,
    get_peewee_database,
)


def migrate_ankihub_db():
    """Migrate the AnkiHub DB to the latest schema version."""

    LOGGER.info(f"AnkiHub DB schema version: {ankihub_db.schema_version()}")

    if ankihub_db.schema_version() < 1:
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

    if ankihub_db.schema_version() < 2:
        with ankihub_db.connection() as conn:
            conn.execute("CREATE INDEX ankihub_deck_id_idx ON notes (ankihub_deck_id)")
            conn.execute("CREATE INDEX anki_note_id_idx ON notes (anki_note_id)")
            conn.execute("PRAGMA user_version = 2;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 3:
        with ankihub_db.connection() as conn:
            conn.execute("ALTER TABLE notes ADD COLUMN guid TEXT")
            conn.execute("ALTER TABLE notes ADD COLUMN fields TEXT")
            conn.execute("ALTER TABLE notes ADD COLUMN tags TEXT")
            conn.execute("PRAGMA user_version = 3;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 4:
        with ankihub_db.connection() as conn:
            conn.execute("ALTER TABLE notes ADD COLUMN last_update_type TEXT")
            conn.execute("PRAGMA user_version = 4;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 5:
        with ankihub_db.connection() as conn:
            conn.execute("CREATE INDEX anki_note_type_id ON notes (anki_note_type_id)")
            conn.execute("PRAGMA user_version = 5;")
        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 6:
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

    if ankihub_db.schema_version() < 7:
        with ankihub_db.connection() as conn:
            # Remove newlines from tags
            conn.execute("UPDATE notes SET tags = REPLACE(tags, '\n', '')")
            conn.execute("PRAGMA user_version = 7;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 8:
        _setup_note_types_table()
        ankihub_db.execute("PRAGMA user_version = 8;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 9:
        _setup_deck_media_table()
        ankihub_db.execute("PRAGMA user_version = 9;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 10:
        # Migrate note_types to new table which has a different primary key
        # To do that in sqlite, we need to create a new table, copy the data over and then delete the old table
        with ankihub_db.connection() as conn:
            # Previously the migration was not wrapped in a transaction and the temp_note_types table was not dropped,
            # so we need to drop it here if it exists
            conn.execute("DROP TABLE IF EXISTS temp_notetypes;")
            conn.execute("ALTER TABLE notetypes RENAME TO temp_notetypes;")
            _setup_note_types_table()
            conn.execute(
                """
                INSERT INTO notetypes (anki_note_type_id, ankihub_deck_id, name, note_type_dict_json)
                SELECT anki_note_type_id, ankihub_deck_id, name, note_type_dict_json
                FROM temp_notetypes;
                """
            )
            conn.execute("DROP TABLE temp_notetypes;")

            conn.execute("PRAGMA user_version = 10;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 11:
        # Migrate tables to ensure that all users have the same schemas which are created by peewee.
        # This for example ensures that column types and index names are the same for all users.
        with get_peewee_database().atomic():
            models_to_migrate: List[BaseModel] = [
                AnkiHubNote,  # type: ignore
                AnkiHubNoteType,  # type: ignore
                DeckMedia,  # type: ignore
            ]
            for model in models_to_migrate:
                table_name = model._meta.table_name
                temp_table_name = f"temp_{table_name}"

                # Rename the current table if it exists
                try:
                    get_peewee_database().execute_sql(
                        f"ALTER TABLE {table_name} RENAME TO {temp_table_name}"
                    )
                except Exception as e:
                    # Renaming a table can't be rolled back in SQLite. If a previous run of this migration
                    # was interrupted after renaming the table, the table will still be have the temp_ prefix.
                    # All other changes in this migration will be rolled back in this case.
                    # This means we can just ignore the error here and continue with the migration.
                    LOGGER.warning(
                        f"Failed to rename table {table_name} to {temp_table_name}: {e}"
                    )

                # Create the new table using peewee
                model.bind(get_peewee_database())
                model.create_table()

                # Copy the data to the new table
                get_peewee_database().execute_sql(
                    f"INSERT INTO {table_name} SELECT * FROM {temp_table_name}"
                )

                # Drop the old table
                get_peewee_database().execute_sql(f"DROP TABLE {temp_table_name}")

            get_peewee_database().execute_sql("PRAGMA user_version = 11;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )

    if ankihub_db.schema_version() < 12:
        # Remove hyphens from uuid columns because peewee's UUIDFields expect uuids to be stored without hyphens
        with get_peewee_database().atomic():
            remove_hyphens_sql = "UPDATE {table_name} SET {column_name} = REPLACE({column_name}, '-', '')"

            # Remove hyphens from the ankihub_note_id column in the notes table
            get_peewee_database().execute_sql(
                remove_hyphens_sql.format(
                    table_name="notes", column_name="ankihub_note_id"
                )
            )

            # Remove hyphens from the ankihub_deck_id column in the notes, notetypes and deck_media tables
            for table_name in ["notes", "notetypes", "deck_media"]:
                get_peewee_database().execute_sql(
                    remove_hyphens_sql.format(
                        table_name=table_name, column_name="ankihub_deck_id"
                    )
                )

            get_peewee_database().execute_sql("PRAGMA user_version = 12;")

        LOGGER.info(
            f"AnkiHub DB migrated to schema version {ankihub_db.schema_version()}"
        )


def _setup_note_types_table() -> None:
    """Create the note types table as it was in schema version 8.""" ""
    ankihub_db.execute(
        """
        CREATE TABLE notetypes (
            anki_note_type_id INTEGER NOT NULL,
            ankihub_deck_id STRING NOT NULL,
            name TEXT NOT NULL,
            note_type_dict_json TEXT NOT NULL,
            PRIMARY KEY (anki_note_type_id, ankihub_deck_id)
        );
        """
    )


def _setup_deck_media_table() -> None:
    """Create the deck_media table as it was in schema version 9."""
    with ankihub_db.connection() as conn:
        conn.execute(
            """
            CREATE TABLE deck_media (
                name TEXT NOT NULL,
                ankihub_deck_id TEXT NOT NULL,
                file_content_hash TEXT,
                modified TIMESTAMP NOT NULL,
                referenced_on_accepted_note BOOLEAN NOT NULL,
                exists_on_s3 BOOLEAN NOT NULL,
                download_enabled BOOLEAN NOT NULL,
                PRIMARY KEY (name, ankihub_deck_id)
            );
            """
        )
        conn.execute(
            "CREATE INDEX deck_media_deck_hash ON deck_media (ankihub_deck_id, file_content_hash);"
        )
        LOGGER.info("Created deck_media table")
