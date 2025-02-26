from typing import Set

from anki.models import NotetypeId


class ChangesRequireFullSyncError(Exception):
    """Raised when a change will require a full sync with AnkiWeb."""

    def __init__(self, affected_note_type_ids: Set[NotetypeId]):
        super().__init__(
            f"Changes related to the following note types require a full sync with AnkiWeb: {affected_note_type_ids}"
        )
        self.affected_note_type_ids = affected_note_type_ids
