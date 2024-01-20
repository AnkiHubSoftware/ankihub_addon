class AnkiHubDBError(Exception):
    pass


class IntegrityError(AnkiHubDBError):
    def __init__(self, message):
        super().__init__(message)
