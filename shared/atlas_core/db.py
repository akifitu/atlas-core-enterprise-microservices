import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class Database:
    def __init__(self, path: str) -> None:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = Lock()

    def executescript(self, script: str) -> None:
        with self._lock:
            self._connection.executescript(script)
            self._connection.commit()

    def execute(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(query, tuple(params))
            self._connection.commit()
            return cursor

    def fetchone(self, query: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(query, tuple(params))
            row = cursor.fetchone()
        return _row_to_dict(row)

    def fetchall(self, query: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(query, tuple(params))
            rows = cursor.fetchall()
        return [_row_to_dict(row) for row in rows if row is not None]  # type: ignore[arg-type]

    def scalar(self, query: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            cursor = self._connection.execute(query, tuple(params))
            row = cursor.fetchone()
        if row is None:
            return None
        return row[0]

    def close(self) -> None:
        with self._lock:
            self._connection.close()
