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


ankihub_db_proxy = Proxy()


class AnkiHubNotes(Model):
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
        database = ankihub_db_proxy


def set_peewee_database():
    ankihub_db = SqliteDatabase(ankihub_db_path())
    ankihub_db_proxy.initialize(ankihub_db)
