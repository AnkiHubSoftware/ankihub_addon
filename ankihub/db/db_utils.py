import sqlite3
from typing import Any, Callable, ContextManager, List, Optional, Tuple

from .. import LOGGER


class DBConnection:
    """A wrapper around a sqlite3.Connection that provides some convenience methods.
    The lock_context is used to wrap calls to self.execute()."""

    def __init__(
        self, conn: sqlite3.Connection, lock_context: Callable[[], ContextManager]
    ):
        self._conn = conn
        self._is_used_as_context_manager = False
        self._lock_context = lock_context

    def execute(
        self,
        sql: str,
        *args,
        first_row_only=False,
    ) -> List:
        with self._lock_context():
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
        finally:
            if not self._is_used_as_context_manager:
                self._conn.commit()
                self._conn.close()

        return result

    def scalar(self, sql: str, *args) -> Any:
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return rows[0]
        else:
            return None

    def list(self, sql: str, *args) -> List:
        return [x[0] for x in self.execute(sql, *args, first_row_only=False)]

    def first(self, sql: str, *args) -> Optional[Tuple]:
        rows = self.execute(sql, *args, first_row_only=True)
        if rows:
            return tuple(rows)
        else:
            return None

    def __enter__(self):
        self._is_used_as_context_manager = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._is_used_as_context_manager = False
        self._conn.commit()
        self._conn.close()
