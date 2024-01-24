from pathlib import Path
from typing import Optional

from peewee import (
    BooleanField,
    CompositeKey,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
    TimestampField,
    UUIDField,
)
from playhouse.shortcuts import ThreadSafeDatabaseMetadata

# This will eventually be set to a peewee database object
_ankihub_db: Optional[SqliteDatabase] = None


class BaseModel(Model):
    class Meta:
        model_metadata_class = ThreadSafeDatabaseMetadata


class AnkiHubNote(BaseModel):
    ankihub_note_id = UUIDField(primary_key=True)
    ankihub_deck_id = UUIDField(index=True, null=True)
    anki_note_id = IntegerField(unique=True, null=True)
    anki_note_type_id = IntegerField(index=True, null=True)
    mod = IntegerField(null=True)
    guid = TextField(null=True)
    fields = TextField(null=True)
    tags = TextField(null=True)
    last_update_type = TextField(null=True)

    class Meta:
        table_name = "notes"


class AnkiHubNoteType(BaseModel):
    anki_note_type_id = IntegerField()
    ankihub_deck_id = UUIDField()
    name = TextField()
    note_type_dict_json = TextField()

    class Meta:
        table_name = "notetypes"
        primary_key = CompositeKey("anki_note_type_id", "ankihub_deck_id")


class DeckMedia(BaseModel):
    name = TextField()
    ankihub_deck_id = UUIDField()
    file_content_hash = TextField(null=True)
    modified = TimestampField()
    referenced_on_accepted_note = BooleanField()
    exists_on_s3 = BooleanField()
    download_enabled = BooleanField()

    class Meta:
        table_name = "deck_media"
        primary_key = CompositeKey("name", "ankihub_deck_id")
        indexes = ((("ankihub_deck_id", "file_content_hash"), False),)


def set_peewee_database(db_path: Path) -> None:
    global _ankihub_db
    _ankihub_db = SqliteDatabase(db_path)


def get_peewee_database() -> SqliteDatabase:
    return _ankihub_db


def create_tables() -> None:
    _ankihub_db.create_tables([AnkiHubNote, AnkiHubNoteType, DeckMedia])


def bind_peewee_models() -> None:
    _ankihub_db.bind([AnkiHubNote, AnkiHubNoteType, DeckMedia])
