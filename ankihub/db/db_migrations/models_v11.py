"""Snapshot of the peewee database models at schema version 11."""

from peewee import BooleanField, CompositeKey, IntegerField, Model, TextField

from ...ankihub_client.models import SuggestionType
from ..models import DateTimeField, JSONField, UUIDField


class AnkiHubNote(Model):
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

    def was_deleted(self) -> bool:
        return self.last_update_type == SuggestionType.DELETE.value[0]


class AnkiHubNoteType(Model):
    anki_note_type_id = IntegerField()
    ankihub_deck_id = UUIDField()
    name = TextField()
    note_type_dict = JSONField(column_name="note_type_dict_json")

    class Meta:
        table_name = "notetypes"
        primary_key = CompositeKey("anki_note_type_id", "ankihub_deck_id")


class DeckMedia(Model):
    name = TextField()
    ankihub_deck_id = UUIDField()
    file_content_hash = TextField(null=True)
    modified = DateTimeField()
    referenced_on_accepted_note = BooleanField()
    exists_on_s3 = BooleanField()
    download_enabled = BooleanField()

    class Meta:
        table_name = "deck_media"
        primary_key = CompositeKey("name", "ankihub_deck_id")
        indexes = ((("ankihub_deck_id", "file_content_hash"), False),)
