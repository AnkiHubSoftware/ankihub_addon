"""Defines classes that represent data returned by the AnkiHub API or sent to it.
Also defines some helper functions."""

import dataclasses
import uuid
from abc import ABC
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set

from mashumaro import field_options
from mashumaro.config import BaseConfig
from mashumaro.mixins.json import DataClassJSONMixin

from ..common_utils import local_media_names_from_html

ANKIHUB_DATETIME_FORMAT_STR = "%Y-%m-%dT%H:%M:%S.%f%z"


class SuggestionType(Enum):
    UPDATED_CONTENT = "updated_content", "Updated content"
    NEW_CONTENT = "new_content", "New content"
    SPELLING_GRAMMATICAL = "spelling/grammar", "Spelling/Grammar"
    CONTENT_ERROR = "content_error", "Content error"
    NEW_CARD_TO_ADD = "new_card_to_add", "New card to add"
    NEW_TAGS = "new_tags", "New Tags"
    UPDATED_TAGS = "updated_tags", "Updated Tags"
    DELETE = "delete", "Delete Note"
    OTHER = "other", "Other"


def suggestion_type_from_str(s: str) -> Optional[SuggestionType]:
    if s in ["original_content", "new_note", None]:
        return None

    result = next((x for x in SuggestionType if x.value[0] == s), None)
    if result is None:
        raise ValueError("Invalid suggestion type string")
    return result


class DataClassJSONMixinWithConfig(DataClassJSONMixin):
    class Config(BaseConfig):
        serialize_by_alias = True


@dataclass
class Field(DataClassJSONMixinWithConfig):
    name: str
    value: str


@dataclass
class NoteInfo(DataClassJSONMixinWithConfig):
    ah_nid: uuid.UUID = dataclasses.field(metadata=field_options(alias="note_id"))
    anki_nid: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    mid: int = dataclasses.field(metadata=field_options(alias="note_type_id"))
    fields: List[Field]
    tags: Optional[List[str]]  # None means no tag changes
    guid: str
    last_update_type: Optional[SuggestionType] = dataclasses.field(
        metadata=field_options(
            serialize=lambda x: x.value[0] if x is not None else None,
            deserialize=suggestion_type_from_str,
        ),
        default=None,
    )


@dataclass
class NoteInfoForUpload(DataClassJSONMixinWithConfig):
    ah_nid_str: str = dataclasses.field(metadata=field_options(alias="id"))
    anki_nid: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    mid: int = dataclasses.field(metadata=field_options(alias="note_type_id"))
    fields: List[Field]
    tags: List[str]
    guid: str


def note_info_for_upload(note_info: NoteInfo) -> NoteInfoForUpload:
    return NoteInfoForUpload(
        ah_nid_str=str(note_info.ah_nid),
        anki_nid=note_info.anki_nid,
        mid=note_info.mid,
        fields=note_info.fields,
        tags=note_info.tags,
        guid=note_info.guid,
    )


class UserDeckRelation(Enum):
    SUBSCRIBER = "subscriber"
    OWNER = "owner"
    MAINTAINER = "maintainer"
    NONE = None


@dataclass
class Deck(DataClassJSONMixinWithConfig):
    ah_did: uuid.UUID = dataclasses.field(metadata=field_options(alias="id"))
    anki_did: int = dataclasses.field(metadata=field_options(alias="anki_id"))
    name: str
    csv_last_upload: datetime = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: (
                datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR) if x else None
            )
        )
    )
    csv_notes_filename: str
    media_upload_finished: bool
    user_relation: UserDeckRelation = dataclasses.field(
        metadata=field_options(
            serialize=lambda x: x.value,
            deserialize=lambda s: UserDeckRelation(s),
        )
    )
    has_note_embeddings: bool = False

    @property
    def is_user_relation_owner_or_maintainer(self):
        return (
            self.user_relation == UserDeckRelation.OWNER
            or self.user_relation == UserDeckRelation.MAINTAINER
        )

    @property
    def is_user_relation_subscriber(self):
        return self.user_relation == UserDeckRelation.SUBSCRIBER

    @property
    def is_user_relation_maintainer(self):
        return self.user_relation == UserDeckRelation.MAINTAINER

    @property
    def is_user_relation_owner(self):
        return self.user_relation == UserDeckRelation.OWNER


@dataclass
class DeckUpdates(DataClassJSONMixinWithConfig):
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: (
                datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR) if x else None
            ),
        ),
    )
    protected_fields: Dict[int, List[str]]
    protected_tags: List[str]
    notes: Optional[List[NoteInfo]]


@dataclass
class DeckUpdatesChunk(DeckUpdates):
    from_csv: bool


@dataclass
class DeckMedia(DataClassJSONMixinWithConfig):
    name: str
    file_content_hash: str
    modified: datetime = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
        ),
    )
    referenced_on_accepted_note: bool
    exists_on_s3: bool
    download_enabled: bool


@dataclass
class DeckMediaUpdateChunk(DataClassJSONMixinWithConfig):
    media: List[DeckMedia]
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
        )
    )


class NotesActionChoices(Enum):
    UNSUSPEND = "unsuspend"


@dataclass
class NotesAction(DataClassJSONMixinWithConfig):
    action: NotesActionChoices
    note_ids: List[uuid.UUID]


@dataclass
class NoteSuggestion(DataClassJSONMixinWithConfig, ABC):
    ah_nid: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="ankihub_id",
            serialize=str,
        ),
    )
    anki_nid: int = dataclasses.field(
        metadata=field_options(
            alias="anki_id",
        )
    )
    fields: List[Field]
    comment: str


@dataclass
class ChangeNoteSuggestion(NoteSuggestion):
    added_tags: List[str]
    removed_tags: List[str]
    change_type: SuggestionType = dataclasses.field(
        metadata=field_options(
            serialize=lambda x: x.value[0],
            deserialize=suggestion_type_from_str,
        ),
    )

    def __post_serialize__(self, d: Dict[Any, Any]) -> Dict[Any, Any]:
        # note_id is needed for bulk change note suggestions
        d["note_id"] = d["ankihub_id"]
        return d


@dataclass
class NewNoteSuggestion(NoteSuggestion):
    ah_did: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="deck_id",
            serialize=str,
        ),
    )
    note_type_name: str = dataclasses.field(
        metadata=field_options(
            alias="note_type",
        )
    )
    anki_note_type_id: int = dataclasses.field(
        metadata=field_options(
            alias="note_type_id",
        )
    )
    tags: Optional[List[str]]  # None means no tag changes
    guid: str


@dataclass
class TagGroupValidationResponse(DataClassJSONMixinWithConfig):
    tag_group_name: str
    success: bool
    deck_extension_id: Optional[int]
    errors: List[str]


@dataclass
class OptionalTagSuggestion(DataClassJSONMixinWithConfig):
    tag_group_name: str
    deck_extension_id: int
    ah_nid: uuid.UUID = dataclasses.field(
        metadata=field_options(
            alias="related_note",
            serialize=str,
        ),
    )
    tags: List[str]


class UserDeckExtensionRelation(Enum):
    SUBSCRIBER = "subscriber"
    OWNER = "owner"
    MAINTAINER = "maintainer"


@dataclass
class DeckExtension(DataClassJSONMixinWithConfig):
    id: int
    ah_did: uuid.UUID = dataclasses.field(metadata=field_options(alias="deck"))
    owner_id: int = dataclasses.field(metadata=field_options(alias="owner"))
    name: str
    tag_group_name: str
    description: str
    user_relation: UserDeckExtensionRelation


@dataclass
class NoteCustomization(DataClassJSONMixinWithConfig):
    ankihub_nid: uuid.UUID = dataclasses.field(metadata=field_options(alias="note"))
    tags: List[str]


@dataclass
class DeckExtensionUpdateChunk(DataClassJSONMixinWithConfig):
    note_customizations: List[NoteCustomization]
    latest_update: Optional[datetime] = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: (
                datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR) if x else None
            ),
        ),
        default=None,
    )


@dataclass
class CardReviewData(DataClassJSONMixinWithConfig):
    ah_did: uuid.UUID = dataclasses.field(metadata=field_options(alias="deck_id"))
    total_card_reviews_last_7_days: int
    total_card_reviews_last_30_days: int
    first_card_review_at: datetime = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
        ),
    )
    last_card_review_at: datetime = dataclasses.field(
        metadata=field_options(
            deserialize=lambda x: datetime.strptime(x, ANKIHUB_DATETIME_FORMAT_STR)
        ),
    )


@dataclass
class DailyCardReviewSummary(DataClassJSONMixinWithConfig):
    review_session_date: date
    total_cards_studied: int = 0
    total_time_reviewing: int = 0
    total_cards_marked_as_again: int = 0
    total_cards_marked_as_hard: int = 0
    total_cards_marked_as_good: int = 0
    total_cards_marked_as_easy: int = 0


# Media related functions


def get_media_names_from_notes_data(notes_data: Sequence[NoteInfo]) -> Set[str]:
    """Return the names of all media files on the given notes.
    Only returns names of local files, not remote files."""
    return {
        name for note in notes_data for name in get_media_names_from_note_info(note)
    }


def get_media_names_from_suggestions(suggestions: Sequence[NoteSuggestion]) -> Set[str]:
    """Return the names of all media files on the given suggestions.
    Only returns names of local files, not remote files."""
    return {
        name
        for suggestion in suggestions
        for name in get_media_names_from_suggestion(suggestion)
    }


def get_media_names_from_suggestion(suggestion: NoteSuggestion) -> Set[str]:
    result = {
        name
        for field in suggestion.fields
        for name in _get_media_names_from_field(field)
    }
    return result


def get_media_names_from_note_info(note_info: NoteInfo) -> Set[str]:
    result = {
        name
        for field in note_info.fields
        for name in _get_media_names_from_field(field)
    }
    return result


def _get_media_names_from_field(field: Field) -> Set[str]:
    """Return the names of all media files on the given field. Only returns names of local files, not remote files."""
    result = local_media_names_from_html(field.value)
    return result
