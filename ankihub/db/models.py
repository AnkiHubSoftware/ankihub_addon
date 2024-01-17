from typing import List
import uuid

from peewee import (
    Model,
    CharField,
    IntegerField,
    SqliteDatabase,
    Proxy,
)
from ..settings import ankihub_db_path


ankihub_db = SqliteDatabase(None)


class AnkiHubNote(Model):
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
        database = ankihub_db


class AnkiHubNoteType(Model):
    anki_note_type_id = IntegerField(primary_key=True)
    ankihub_deck_id = CharField()
    name = CharField()
    note_type_dict_json = CharField()

    class Meta:
        table_name = "notetypes"
        database = ankihub_db


def set_peewee_database():
    ankihub_db.init(ankihub_db_path())
