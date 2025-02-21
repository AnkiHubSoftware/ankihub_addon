from typing import List


class ChangesRequireFullSyncError(Exception):
    """Raised when a change will require a full sync with AnkiWeb."""

    def __init__(self, changes: List[str]):
        super().__init__(
            f"Changes require a full sync with AnkiWeb: {', '.join(changes)}"
        )
        self.changes = changes
