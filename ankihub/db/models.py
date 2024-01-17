from typing import List
import uuid

from peewee import (
    Model,
    BooleanField,
    CharField,
    CompositeKey,
    IntegerField,
    SqliteDatabase,
    TextField,
    TimestampField,
)
from playhouse.shortcuts import ThreadSafeDatabaseMetadata
from ..settings import ankihub_db_path


ankihub_db = SqliteDatabase(None)


class BaseModel(Model):
    class Meta:
        model_metadata_class = ThreadSafeDatabaseMetadata
        database = ankihub_db


class AnkiHubNote(BaseModel):
    ankihub_note_id = CharField(primary_key=True)
    ankihub_deck_id = CharField()
    anki_note_id = IntegerField(unique=True)
    anki_note_type_id = IntegerField()
    mod = IntegerField()
    guid = CharField()
    fields = CharField()
    tags = CharField()
    last_update_type = CharField()

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


def set_peewee_database():
    ankihub_db.init(ankihub_db_path())
