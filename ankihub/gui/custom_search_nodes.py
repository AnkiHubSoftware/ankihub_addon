from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from anki.utils import ids2str
from aqt import mw
from aqt.browser import Browser, ItemId

from ..ankihub_client import suggestion_type_from_str


class CustomSearchNode(ABC):

    parameter_name: Optional[str] = None
    browser: Optional[Browser] = None

    @classmethod
    def from_parameter_type_and_value(cls, browser, parameter_name, value):
        custom_search_node_types = (
            ModifiedAfterSyncSearchNode,
            UpdatedInTheLastXDaysSearchNode,
            SuggestionTypeSearchNode,
            UpdatedSinceLastReviewSearchNode,
        )
        for custom_search_node_type in custom_search_node_types:
            if custom_search_node_type.parameter_name == parameter_name:
                return custom_search_node_type(browser, value)  # type: ignore

        raise ValueError(f"Unknown custom search parameter: {parameter_name}")

    @abstractmethod
    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        # Filters the given ids to only those that match the custom search node.
        # Expects the ankihub database to be attached to the anki database connection.
        # Ids can be either note ids or card ids.
        pass

    def _retain_ids_where(self, ids: Sequence[ItemId], where: str) -> Sequence[ItemId]:
        # Returns these ids that match the given where clause
        # while joining notes with their corresponding information from the ankihub database.
        # The provided ids can be either note ids or card ids.
        # The anki notes table can be accessed with the table name "notes",
        # cards can be accessed as "cards" and the ankihub notes
        # table can be accessed as "ah_notes".
        if self.browser.table.is_notes_mode():
            nids = mw.col.db.list(
                "SELECT id FROM notes, ankihub_db.notes as ah_notes "
                "WHERE notes.id = ah_notes.anki_note_id AND "
                f"notes.id IN {ids2str(ids)} AND " + where
            )
            return nids
        else:
            # this approach is faster than joining notes with cards in the query,
            # but maybe this wouldn't be the case if the query were written better
            nids = mw.col.db.list(
                "SELECT DISTINCT nid FROM cards WHERE id IN " + ids2str(ids)
            )
            selected_note_ids = mw.col.db.list(
                "SELECT id FROM notes, ankihub_db.notes as ah_notes "
                "WHERE notes.id = ah_notes.anki_note_id AND "
                f"notes.id IN {ids2str(nids)} AND " + where
            )
            cids = mw.col.db.list(
                "SELECT id FROM cards WHERE nid IN " + ids2str(selected_note_ids)
            )
            return cids


class ModifiedAfterSyncSearchNode(CustomSearchNode):

    parameter_name = "ankihub_modified_after_sync"

    def __init__(self, browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value == "yes":
            ids = self._retain_ids_where(ids, "notes.mod > ah_notes.mod")
        elif self.value == "no":
            ids = self._retain_ids_where(ids, "notes.mod <= ah_notes.mod")
        else:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. Options are 'yes' and 'no'."
            )

        return ids


class UpdatedInTheLastXDaysSearchNode(CustomSearchNode):

    parameter_name = "ankihub_updated"

    def __init__(self, browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        try:
            days = int(self.value)
            if days <= 0:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. Must be a positive integer."
            )

        threshold_timestamp = int(datetime.now().timestamp() - (days * 24 * 60 * 60))
        ids = self._retain_ids_where(ids, f"ah_notes.mod > {threshold_timestamp}")

        return ids


class SuggestionTypeSearchNode(CustomSearchNode):

    parameter_name = "ankihub_suggestion_type"

    def __init__(self, browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        value = self.value.replace("_slash_", "/")
        try:
            suggestion_type_from_str(value)
        except ValueError:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {value}. Must be a suggestion type."
            )

        ids = self._retain_ids_where(ids, f"ah_notes.last_update_type = '{value}'")

        return ids


class UpdatedSinceLastReviewSearchNode(CustomSearchNode):

    parameter_name = "ankihub_updated_since_last_review"

    def __init__(self, browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value.strip() == "":
            ids = self._retain_ids_where(
                ids,
                """
                ah_notes.mod > (
                    SELECT max(revlog.id) FROM revlog, cards
                    WHERE revlog.cid = cards.id AND cards.nid = notes.id
                ) / 1000
                """,
            )
        else:
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. This search parameter takes no values."
            )

        return ids
