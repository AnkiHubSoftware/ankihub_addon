from aqt.operations import QueryOp

from ...settings import ANKI_INT_VERSION, ANKI_VERSION_23_10_00


class AddonQueryOp(QueryOp):
    """A subclass of aqt.operations.QueryOp that is tailored for the AnkiHub add-on."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # The default behavior of the QueryOp class on a failure is to show the exception to the user.
        # However we want to raise the exception so that our central error handler can handle it.
        self._failure = _on_failure

    def without_collection(self):
        """The QueryOp class doesn't have a without_collection method on Anki versions before 23.10.
        We are setting this to be a no-op for backwards compatibility.
        It's fine for this to be a no-op, because without_collection is used to allow
        background tasks to run in parallel. On previous Anki versions background tasks were
        already running in parallel, so there is no need to do anything.
        This way we can use the same code for all Anki versions.
        """
        if ANKI_INT_VERSION < ANKI_VERSION_23_10_00:
            return self

        return super().without_collection()


def _on_failure(exception: Exception) -> None:
    raise exception
