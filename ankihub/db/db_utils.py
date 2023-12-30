import sqlite3
from typing import Any, Callable, ContextManager, List, Optional, Tuple

from .. import LOGGER


class DBConnection:
    """A wrapper around a sqlite3.Connection that provides convenience methods for
    executing queries and handling transactions.
    This class can be used as a context manager, in which case all queries executed within
    the context will be part of a single transaction. If an exception occurs within the context,
    the transaction will be automatically rolled back.

    thread_safety_context_func: A function that returns a context manager. This is used to ensure that
    threads only access the database when it is safe to do so.

    Note: Once a query has been executed using an instance of this class,
    the instance cannot be used to execute another query unless it is within a context manager.
    Attempting to do so will raise an exception.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        thread_safety_context_func: Callable[[], ContextManager],
    ):
        self._conn = conn
        self._is_used_as_context_manager = False
        self._thread_safety_context_func = thread_safety_context_func
        self._thread_safety_context: Optional[ContextManager] = None

    def execute(
        self,
        sql: str,
        *args,
        first_row_only=False,
    ) -> List:
        if self._is_used_as_context_manager:
            return self._execute_inner(sql, *args, first_row_only=first_row_only)
        else:
            with self._thread_safety_context_func():
                return self._execute_inner(sql, *args, first_row_only=first_row_only)

    def _execute_inner(self, sql: str, *args, first_row_only=False) -> List:
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
        finally:
            if not self._is_used_as_context_manager:
                self._conn.close()

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
        self._thread_safety_context = self._thread_safety_context_func()
        self._thread_safety_context.__enter__()
        self._is_used_as_context_manager = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

        self._conn.close()
        self._is_used_as_context_manager = False
        self._thread_safety_context.__exit__(exc_type, exc_val, exc_tb)
