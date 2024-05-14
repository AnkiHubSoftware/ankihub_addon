from uuid import UUID


class DeckDownloadAndInstallError(Exception):
    """Raised when an error occurs while downloading and installing a deck."""

    def __init__(self, original_exception: Exception, ankihub_did: UUID):
        super().__init__(
            f"Error while downloading and installing deck {ankihub_did}: {original_exception}"
        )
        self.original_exception = original_exception
        self.__cause__ = original_exception
        self.ankihub_did = ankihub_did


class RemoteDeckNotFoundError(Exception):
    """Raised when a deck doesn't exist on AnkiHub (anymore)."""

    def __init__(self, ankihub_did: UUID):
        super().__init__(f"Deck {ankihub_did} not found on AnkiHub.")
        self.ankihub_did = ankihub_did


class FullSyncCancelled(Exception):
    """Raised when a full AnkiWeb sync is cancelled before an AnkiHub sync."""

    pass
