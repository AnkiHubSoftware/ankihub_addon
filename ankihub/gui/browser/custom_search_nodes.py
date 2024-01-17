"""Custom search nodes for the browser.
Search nodes are used to define search parameters for the Anki browser search bar."""
import operator
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Sequence, cast

import aqt
from anki.notes import NoteId
from anki.utils import ids2str
from aqt.browser import Browser, ItemId

from ...ankihub_client import suggestion_type_from_str
from ...db import ankihub_db


class CustomSearchNode(ABC):
    parameter_name: str = None
    browser: Browser = None

    @classmethod
    def from_parameter_type_and_value(cls, browser, parameter_name, value):
        custom_search_node_types = (
            ModifiedAfterSyncSearchNode,
            UpdatedInTheLastXDaysSearchNode,
            SuggestionTypeSearchNode,
            NewNoteSearchNode,
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

    def _note_ids(self, ids: Sequence[ItemId]) -> Sequence[NoteId]:
        """Converts the given card ids to note ids if the browser is in card mode.
        Otherwise returns the given note ids."""
        if self.browser.table.is_notes_mode():
            result = cast(Sequence[NoteId], ids)
        else:
            result = aqt.mw.col.db.list(
                f"SELECT DISTINCT nid FROM cards WHERE id IN {ids2str(ids)}"
            )
        return result

    def _output_ids(self, note_ids: Sequence[NoteId]) -> Sequence[ItemId]:
        """Converts the given note ids to card ids if the browser is in card mode.
        Otherwise returns the given note ids."""
        if self.browser.table.is_notes_mode():
            return note_ids

        result = aqt.mw.col.db.list(
            f"SELECT id from cards WHERE nid in {ids2str(note_ids)}"
        )
        return result


class ModifiedAfterSyncSearchNode(CustomSearchNode):
    parameter_name = "ankihub_modified_after_sync"

    def __init__(self, browser: Browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value not in ("yes", "no"):
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. Options are 'yes' and 'no'."
            )

        nids = self._note_ids(ids)

        nid_to_ah_mod: Dict[NoteId, int] = dict(
            ankihub_db.execute(
                f"""
                SELECT anki_note_id, mod FROM notes
                WHERE anki_note_id in {ids2str(nids)}
                """
            )
        )

        nid_to_anki_mod: Dict[NoteId, int] = dict(
            aqt.mw.col.db.all(  # type: ignore
                f"""
                SELECT id, mod FROM notes
                WHERE id in {ids2str(nid_to_ah_mod.keys())}
                """
            )
        )

        retain_notes_modified_after_sync = self.value == "yes"
        op = operator.gt if retain_notes_modified_after_sync else operator.le
        retained_nids = [
            nid
            for nid, anki_mod in nid_to_anki_mod.items()
            if nid in nid_to_ah_mod and op(anki_mod, nid_to_ah_mod[nid])
        ]

        result = self._output_ids(retained_nids)
        return result


class UpdatedInTheLastXDaysSearchNode(CustomSearchNode):
    parameter_name = "ankihub_updated"

    def __init__(self, browser: Browser, value: str):
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

        threshold_timestamp = int(
            (
                datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=days - 1)
            ).timestamp()
        )

        nids = self._note_ids(ids)

        retained_nids = ankihub_db.list(
            f"""
            SELECT anki_note_id FROM notes
            WHERE anki_note_id in {ids2str(nids)} AND
            mod >= {threshold_timestamp}
            """
        )

        result = self._output_ids(retained_nids)
        return result


class NewNoteSearchNode(CustomSearchNode):
    parameter_name = "ankihub_new_note"

    def __init__(self, browser: Browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value.strip() != "":
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. This search parameter takes no values."
            )

        nids = self._note_ids(ids)

        retained_nids = ankihub_db.list(
            f"""
            SELECT anki_note_id FROM notes
            WHERE anki_note_id in {ids2str(nids)} AND
            last_update_type IS NULL
            """
        )

        result = self._output_ids(retained_nids)
        return result


class SuggestionTypeSearchNode(CustomSearchNode):
    parameter_name = "ankihub_suggestion_type"

    def __init__(self, browser: Browser, value: str):
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

        nids = self._note_ids(ids)

        retained_nids = ankihub_db.list(
            f"""
            SELECT anki_note_id FROM notes
            WHERE anki_note_id in {ids2str(nids)} AND
            last_update_type = '{value}'
            """
        )

        result = self._output_ids(retained_nids)
        return result


class UpdatedSinceLastReviewSearchNode(CustomSearchNode):
    parameter_name = "ankihub_updated_since_last_review"

    def __init__(self, browser: Browser, value: str):
        self.browser = browser
        self.value = value

    def filter_ids(self, ids: Sequence[ItemId]) -> Sequence[ItemId]:
        if self.value.strip() != "":
            raise ValueError(
                f"Invalid value for {self.parameter_name}: {self.value}. This search parameter takes no values."
            )

        nids = self._note_ids(ids)

        nid_to_ah_mod: Dict[NoteId, int] = dict(
            ankihub_db.execute(
                f"""
                SELECT anki_note_id, mod FROM notes
                WHERE anki_note_id in {ids2str(nids)}
                """
            )
        )

        # The id column of the revlog table is an epoch timestamp in milliseconds of when the review was done.
        nid_to_last_review_timestamp_ms: Dict[NoteId, int] = dict(
            aqt.mw.col.db.all(  # type: ignore
                f"""
                SELECT notes.id, max(revlog.id)
                FROM notes
                JOIN cards ON cards.nid = notes.id
                JOIN revlog ON revlog.cid = cards.id
                WHERE notes.id IN {ids2str(nid_to_ah_mod.keys())}
                GROUP BY notes.id
                """
            )
        )

        retained_nids = [
            nid
            for nid, ah_mod in nid_to_ah_mod.items()
            if nid in nid_to_last_review_timestamp_ms
            and ah_mod >= nid_to_last_review_timestamp_ms[nid] / 1000
        ]

        result = self._output_ids(retained_nids)
        return result
