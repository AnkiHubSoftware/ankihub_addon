import uuid


class AnkiHubDBError(Exception):
    pass


class IntegrityError(AnkiHubDBError):
    def __init__(self, message):
        super().__init__(message)


class MissingValueError(AnkiHubDBError):
    """Is raised when an object in the DB has a missing value that is expected to be non-null."""

    def __init__(self, ah_did: uuid.UUID):
        self.ah_did = ah_did
