"""Custom Anki browser columns."""
import uuid
from abc import abstractmethod
from typing import Optional, Sequence

import aqt
from anki.collection import BrowserColumns
from anki.notes import Note
from anki.utils import ids2str
from aqt.browser import Browser, CellRow, Column, ItemId

from ...db import ankihub_db
from ...utils import note_types_with_ankihub_id_field


class CustomColumn:
    builtin_column: Column

    def on_browser_did_fetch_row(
        self,
        browser: Browser,
        item_id: ItemId,
        row: CellRow,
        active_columns: Sequence[str],
    ) -> None:
        if (
            index := active_columns.index(self.key)
            if self.key in active_columns
            else None
        ) is None:
            return

        note = browser.table._state.get_note(item_id)
        try:
            value = self._display_value(note)
            row.cells[index].text = value
        except Exception as error:
            row.cells[index].text = str(error)

    @property
    def key(self):
        return self.builtin_column.key

    @abstractmethod
    def _display_value(
        self,
        note: Note,
    ) -> str:
        raise NotImplementedError

    def order_by_str(self) -> Optional[str]:
        """Return the SQL string that will be appended after "ORDER BY" to the query that
        fetches the search results when sorting by this column."""
        return None


class AnkiHubIdColumn(CustomColumn):
    builtin_column = Column(
        key="ankihub_id",
        cards_mode_label="AnkiHub ID",
        notes_mode_label="AnkiHub ID",
        sorting=BrowserColumns.SORTING_NONE,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" in note:
            if note["ankihub_id"]:
                return note["ankihub_id"]
            else:
                return "ID Pending"
        else:
            return "Not AnkiHub Note Type"


class EditedAfterSyncColumn(CustomColumn):
    builtin_column = Column(
        key="edited_after_sync",
        cards_mode_label="AnkiHub: Modified After Sync",
        notes_mode_label="AnkiHub: Modified After Sync",
        sorting=BrowserColumns.SORTING_DESCENDING,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" not in note or not note["ankihub_id"]:
            return "N/A"

        last_sync = ankihub_db.last_sync(uuid.UUID(note["ankihub_id"]))
        if last_sync is None:
            # The sync_mod value can be None if the note was synced with an early version of the AnkiHub add-on
            return "Unknown"

        return "Yes" if note.mod > last_sync else "No"

    def order_by_str(self) -> str:
        mids = note_types_with_ankihub_id_field()
        if not mids:
            return None

        return f"""
            (
                SELECT n.mod > ah_n.mod from {ankihub_db.database_name}.notes AS ah_n
                WHERE ah_n.anki_note_id = n.id LIMIT 1
            ) DESC,
            (n.mid IN {ids2str(mids)}) DESC
            """


class UpdatedSinceLastReviewColumn(CustomColumn):
    builtin_column = Column(
        key="updated_since_last_review",
        cards_mode_label="AnkiHub: Updated Since Last Review",
        notes_mode_label="AnkiHub: Updated Since Last Review",
        sorting=BrowserColumns.SORTING_NONE,
        uses_cell_font=False,
        alignment=BrowserColumns.ALIGNMENT_CENTER,
    )

    def _display_value(
        self,
        note: Note,
    ) -> str:
        if "ankihub_id" not in note or not note["ankihub_id"]:
            return "N/A"

        last_sync = ankihub_db.last_sync(uuid.UUID(note["ankihub_id"]))
        if last_sync is None:
            # The sync_mod value can be None if the note was synced with an early version of the AnkiHub add-on
            return "Unknown"

        last_review_ms = aqt.mw.col.db.scalar(
            f"""
            SELECT max(revlog.id) FROM revlog, cards
            WHERE {note.id} = cards.nid AND cards.id = revlog.cid
            """,
        )
        if last_review_ms is None:
            return "No"

        last_review = last_review_ms // 1000

        return "Yes" if last_sync > last_review else "No"