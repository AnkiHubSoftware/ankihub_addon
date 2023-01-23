from typing import Dict, List, Optional

from anki.notes import NoteId
from anki.utils import ids2str
from aqt import mw

from .addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from .ankihub_client import OptionalTagSuggestion, TagGroupValidationResponse
from .db import ankihub_db
from .note_conversion import TAG_FOR_OPTIONAL_TAGS, is_optional_tag


class OptionalTagsSuggestionHelper:
    """Helper class for suggesting optional tags for a set of notes."""

    def __init__(self, nids: List[NoteId]):
        self._nids = nids

        ankihub_dids = ankihub_db.ankihub_dids_for_anki_nids(self._nids)
        if len(ankihub_dids) == 0:
            raise ValueError("No AnkiHub deck found for these notes.")
        if len(ankihub_dids) > 1:
            raise ValueError("Multiple AnkiHub decks found for these notes.")

        self._ankihub_did = ankihub_dids[0]

        self._optional_tags_by_nid = self._optional_tags_by_nid_dict()
        self._tag_groups = self._extract_optional_tag_groups(self._optional_tags_by_nid)
        self._valid_tag_groups: Optional[List[str]] = None
        self._extension_id_by_tag_group: Optional[Dict[str, int]] = None

    def tag_group_names(self) -> List[str]:
        return self._tag_groups

    def prevalidate(self) -> List[TagGroupValidationResponse]:
        client = AnkiHubClient()
        result: List[TagGroupValidationResponse] = client.prevalidate_tag_groups(
            ankihub_deck_uuid=self._ankihub_did,
            tag_group_names=self._tag_groups,
        )

        self._valid_tag_groups = [
            response.tag_group_name for response in result if response.success
        ]
        self._extension_id_by_tag_group = {
            response.tag_group_name: response.deck_extension_id
            for response in result
            if response.success
        }

        return result

    def suggest_valid_tags(self, auto_accept: bool) -> None:
        # has to be called after self.prevalidate
        assert self._valid_tag_groups is not None

        suggestions: List[OptionalTagSuggestion] = []
        for nid, optional_tags in self._optional_tags_by_nid.items():
            tag_groups_for_note = self._extract_optional_tag_groups(
                {nid: optional_tags}
            )
            for tag_group in tag_groups_for_note:
                if tag_group not in self._valid_tag_groups:
                    continue

                tags_for_group = [
                    tag
                    for tag in optional_tags
                    if tag.startswith(f"{TAG_FOR_OPTIONAL_TAGS}::{tag_group}::")
                ]

                suggestions.append(
                    OptionalTagSuggestion(
                        tag_group_name=tag_group,
                        deck_extension_id=self._extension_id_by_tag_group[tag_group],
                        ankihub_note_uuid=ankihub_db.ankihub_nid_for_anki_nid(nid),
                        tags=tags_for_group,
                    ),
                )

        client = AnkiHubClient()
        client.suggest_optional_tags(
            suggestions=suggestions,
            auto_accept=auto_accept,
        )

    def _optional_tags_by_nid_dict(self) -> Dict[NoteId, List[str]]:
        nid_tags_string_tuples = mw.col.db.all(
            f"SELECT DISTINCT id, tags FROM NOTES WHERE id IN {ids2str(self._nids)} "
            f"AND tags LIKE '%{TAG_FOR_OPTIONAL_TAGS}%'"
        )

        result = {}
        for nid, tags_string in nid_tags_string_tuples:
            tags = mw.col.tags.split(tags_string)
            optional_tags = [
                tag
                for tag in tags
                # optional tags should have at least 3 parts, that's what we are checking for with the split
                # invalid tags will be ignored this way
                if is_optional_tag(tag) and len(tag.split("::", maxsplit=2)) == 3
            ]
            result[nid] = optional_tags

        return result

    def _extract_optional_tag_groups(
        self, optional_tags_by_nid: Dict[NoteId, List[str]]
    ) -> List[str]:
        result = set()
        for _, optional_tags in optional_tags_by_nid.items():
            optional_tag_groups = [
                tag.split("::", maxsplit=2)[1] for tag in optional_tags
            ]
            result.update(optional_tag_groups)

        return list(result)
