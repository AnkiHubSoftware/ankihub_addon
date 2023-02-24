import sqlite3
from typing import Any, List, Optional, Tuple


class DBConnection:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._is_used_as_context_manager = False

    def execute(
        self,
        sql: str,
        *args,
        first_row_only=False,
    ) -> List:
        c = self._conn.cursor()
        c.execute(sql, args)
        if first_row_only:
            result = c.fetchone()
        else:
            result = c.fetchall()
        c.close()

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
