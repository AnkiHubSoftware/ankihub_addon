"""Contains code that is used for converting Anki Note objects to NoteInfo objects (export)
and from NoteInfo objects to Anki note objects (import)."""

from .ankihub_client import SuggestionType

ADDON_INTERNAL_TAG_PREFIX = "AnkiHub_"

TAG_FOR_PROTECTING_FIELDS = f"{ADDON_INTERNAL_TAG_PREFIX}Protect"
TAG_FOR_PROTECTING_ALL_FIELDS = f"{TAG_FOR_PROTECTING_FIELDS}::All"

TAG_FOR_UPDATES = f"{ADDON_INTERNAL_TAG_PREFIX}Update"
TAG_FOR_NEW_NOTE = f"{TAG_FOR_UPDATES}::New_Note"
TAG_FOR_SUGGESTION_TYPE = {
    SuggestionType.UPDATED_CONTENT: f"{TAG_FOR_UPDATES}::Content::Updated",
    SuggestionType.NEW_CONTENT: f"{TAG_FOR_UPDATES}::Content::New",
    SuggestionType.CONTENT_ERROR: f"{TAG_FOR_UPDATES}::Content::Error",
    SuggestionType.SPELLING_GRAMMATICAL: f"{TAG_FOR_UPDATES}::Spelling/Grammar",
    SuggestionType.NEW_TAGS: f"{TAG_FOR_UPDATES}::New_tags",
    SuggestionType.UPDATED_TAGS: f"{TAG_FOR_UPDATES}::Updated_tags",
    SuggestionType.NEW_CARD_TO_ADD: f"{TAG_FOR_UPDATES}::New_Card",
    SuggestionType.OTHER: f"{TAG_FOR_UPDATES}::Other",
}

# top-level tags that are only used by the add-on, but not by the web app
ADDON_INTERNAL_TAGS = [
    TAG_FOR_PROTECTING_FIELDS,
    TAG_FOR_UPDATES,
]

# tags that are used internally by Anki and should not be deleted or appear in suggestions
ANKI_INTERNAL_TAGS = ["leech", "marked"]


def is_internal_tag(tag: str) -> bool:
    return any(
        tag == internal_tag or tag.startswith(f"{internal_tag}::")
        for internal_tag in [*ADDON_INTERNAL_TAGS]
    ) or any(tag == internal_tag for internal_tag in ANKI_INTERNAL_TAGS)
