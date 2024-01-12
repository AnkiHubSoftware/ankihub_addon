from typing import Dict, List, Optional

import aqt
from anki.notes import NoteId
from anki.utils import ids2str

from ..addon_ankihub_client import AddonAnkiHubClient as AnkiHubClient
from ..ankihub_client import OptionalTagSuggestion, TagGroupValidationResponse
from ..db import ankihub_db
from ..settings import config
from .note_conversion import TAG_FOR_OPTIONAL_TAGS, is_optional_tag, is_tag_for_group


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

        self._tag_group_names_from_tags = self._extract_optional_tag_group_names(
            self._optional_tags_by_nid
        )
        deck_extensions_ids_for_deck = config.deck_extensions_ids_for_ah_did(
            self._ankihub_did
        )
        self._tag_group_names_from_config = {
            config.deck_extension_config(deck_extension_id).tag_group_name
            for deck_extension_id in deck_extensions_ids_for_deck
        }
        # self._all_tag_group_names contains both:
        # - the tag group names of all deck extensions that are associated with the AnkiHub deck (data from config)
        # - tag group names which just appear in the optional tags of the notes
        # We need all tag groups from the config to be able to suggest the removal of all optional tags
        # from a note.
        # We need all tag group names from notes to be able to show a warning if a user tries to create
        # optional tags for a tag group which has some problem (e.g. user is not subscribed to it or mistyped the name).
        self._all_tag_group_names = list(
            set(self._tag_group_names_from_config)
            | set(self._tag_group_names_from_tags)
        )

        self._extension_id_by_tag_group_name: Optional[Dict[str, int]] = None
        self._valid_tag_group_names: Optional[List[str]] = None

    def tag_group_names(self) -> List[str]:
        return self._all_tag_group_names

    def prevalidate_tag_groups(self) -> List[TagGroupValidationResponse]:
        """Prevalidate the tag groups and return a list of validation responses.
        Has to be called before self.suggest_tags_for_groups().
        Updates self._valid_tag_group_names and self._extension_id_by_tag_group_name."""
        client = AnkiHubClient()
        result: List[TagGroupValidationResponse] = client.prevalidate_tag_groups(
            ah_did=self._ankihub_did,
            tag_group_names=self._all_tag_group_names,
        )
        self._valid_tag_group_names = [
            response.tag_group_name for response in result if response.success
        ]
        self._extension_id_by_tag_group_name = {
            response.tag_group_name: response.deck_extension_id
            for response in result
            if response.success
        }
        return result

    def suggest_tags_for_groups(self, tag_groups: List[str], auto_accept: bool) -> None:
        """Suggest optional tags for the given tag groups.
        self.prevalidate_tag_groups() needs to be called before this method to validate the tag groups.
        """
        assert self._valid_tag_group_names is not None
        assert set(tag_groups).issubset(set(self._valid_tag_group_names))

        suggestions: List[OptionalTagSuggestion] = []

        for tag_group in tag_groups:
            for nid in self._nids:
                optional_tags_for_nid = self._optional_tags_by_nid.get(nid, [])
                tags_for_group = [
                    tag
                    for tag in optional_tags_for_nid
                    if is_tag_for_group(tag, tag_group)
                ]

                suggestions.append(
                    OptionalTagSuggestion(
                        tag_group_name=tag_group,
                        deck_extension_id=self._extension_id_by_tag_group_name[
                            tag_group
                        ],
                        ah_nid=ankihub_db.ankihub_nid_for_anki_nid(nid),
                        tags=tags_for_group,
                    ),
                )

        client = AnkiHubClient()
        client.suggest_optional_tags(
            suggestions=suggestions,
            auto_accept=auto_accept,
        )

    def _optional_tags_by_nid_dict(self) -> Dict[NoteId, List[str]]:
        """Returns a dict mapping note ids to a list of optional tags for that note."""
        nid_tags_string_tuples = aqt.mw.col.db.all(
            f"SELECT DISTINCT id, tags FROM NOTES WHERE id IN {ids2str(self._nids)} "
            f"AND tags LIKE '%{TAG_FOR_OPTIONAL_TAGS}%'"
        )

        result = {}
        for nid, tags_string in nid_tags_string_tuples:
            tags = aqt.mw.col.tags.split(tags_string)
            optional_tags = [
                tag
                for tag in tags
                # optional tags should have at least 3 parts, that's what we are checking for with the split
                # invalid tags will be ignored this way
                if is_optional_tag(tag) and len(tag.split("::", maxsplit=2)) == 3
            ]
            result[nid] = optional_tags

        return result

    def _extract_optional_tag_group_names(
        self, optional_tags_by_nid: Dict[NoteId, List[str]]
    ) -> List[str]:
        """Extracts the tag group names from the optional tags of the given notes."""
        result = set()
        for _, optional_tags in optional_tags_by_nid.items():
            optional_tag_groups = [
                self._optional_tag_to_tag_group(tag) for tag in optional_tags
            ]
            result.update(optional_tag_groups)

        return list(result)

    def _optional_tag_to_tag_group(self, optional_tag: str) -> str:
        """Extracts the tag group name from the given optional tag."""
        return optional_tag.split("::", maxsplit=2)[1]
