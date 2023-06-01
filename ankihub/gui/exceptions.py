from uuid import UUID


class DeckDownloadAndInstallError(Exception):
    """Raised when an error occurs while downloading and installing a deck."""

    def __init__(self, original_exception: Exception, ankihub_did: UUID):
        super().__init__(
            f"Error while downloading and installing deck {ankihub_did}: {original_exception}"
        )
        self.original_exception = original_exception
        self.ankihub_did = ankihub_did
