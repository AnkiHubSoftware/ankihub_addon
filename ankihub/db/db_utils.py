from typing import Any, List, Optional, Tuple

from .. import LOGGER
from .models import get_peewee_database


class DBConnection:
    """A wrapper around a sqlite3.Connection that provides convenience methods for
    executing queries and handling transactions.
    This class can be used as a context manager, in which case all queries executed within
    the context will be part of a single transaction. If an exception occurs within the context,
    the transaction will be automatically rolled back.

    Note: Once a query has been executed using an instance of this class,
    the instance cannot be used to execute another query unless it is within a context manager.
    Attempting to do so will raise an exception.
    """

    def __init__(self):
        self._conn = get_peewee_database().connection()
        self._is_used_as_context_manager = False

    def execute(
        self,
        sql: str,
        *args,
        first_row_only=False,
    ) -> List:
        try:
            cur = self._conn.cursor()
            cur.execute(sql, args)
            if first_row_only:
                result = cur.fetchone()
            else:
                result = cur.fetchall()
            cur.close()
        except Exception as e:
            LOGGER.info(f"Error while executing SQL: {sql}")
            raise e
        else:
            if not self._is_used_as_context_manager:
                self._conn.commit()

        return result

    def scalar(self, sql: str, *args) -> Any:
        """Returns the first column of the first row of the result set, or None if the result set is empty."""
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return rows[0]
        else:
            return None

    def list(self, sql: str, *args) -> List:
        """Returns the first column of each row of the result set as a list."""
        return [x[0] for x in self.execute(sql, *args, first_row_only=False)]

    def first(self, sql: str, *args) -> Optional[Tuple]:
        """Returns the first row of the result set, or None if the result set is empty."""
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return tuple(rows)
        else:
            return None

    def __enter__(self):
        """
        Temporarily changes connection isolation level from None to DEFERRED

        A non-None isolation level will auto-open transactions before INSERT, UPDATE, DELETE, and REPLACE:
        https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.execute
        """
        self._conn.isolation_level = "DEFERRED"
        self._is_used_as_context_manager = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Changes connection isolation level back to None (peewee expects this)
        """
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

        self._conn.isolation_level = None
