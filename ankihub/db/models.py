from pathlib import Path

from peewee import (
    BooleanField,
    CharField,
    CompositeKey,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
    TimestampField,
)
from playhouse.shortcuts import ThreadSafeDatabaseMetadata

_ankihub_db = None  # This will eventually be set to a peewee database object


class BaseModel(Model):
    class Meta:
        model_metadata_class = ThreadSafeDatabaseMetadata


class AnkiHubNote(BaseModel):
    ankihub_note_id = CharField(primary_key=True)
    ankihub_deck_id = CharField()
    anki_note_id = IntegerField(unique=True)
    anki_note_type_id = IntegerField(index=True)
    mod = IntegerField(null=True)
    guid = CharField()
    fields = CharField()
    tags = CharField()
    last_update_type = CharField(null=True)

    class Meta:
        table_name = "notes"


class AnkiHubNoteType(BaseModel):
    anki_note_type_id = IntegerField()
    ankihub_deck_id = CharField()
    name = CharField()
    note_type_dict_json = CharField()

    class Meta:
        table_name = "notetypes"
        primary_key = CompositeKey("anki_note_type_id", "ankihub_deck_id")


class DeckMedia(BaseModel):
    name = TextField()
    ankihub_deck_id = TextField()
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


def create_tables() -> None:
    _ankihub_db.create_tables([AnkiHubNote, AnkiHubNoteType, DeckMedia])


def bind_peewee_models() -> None:
    _ankihub_db.bind([AnkiHubNote, AnkiHubNoteType, DeckMedia])


def get_peewee_database() -> SqliteDatabase:
    return _ankihub_db
