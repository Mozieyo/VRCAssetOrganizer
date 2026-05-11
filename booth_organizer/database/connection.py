from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from PySide6.QtCore import QMutex, QMutexLocker


class DatabaseManager:
    def __init__(self, db_path: Path):
        self._db_path = str(db_path)
        self._local = threading.local()
        self._write_mutex = QMutex()

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._get_connection()
        try:
            yield conn
        finally:
            pass  # connections are long-lived per thread, don't close

    @contextmanager
    def write_connection(self) -> Generator[sqlite3.Connection, None, None]:
        locker = QMutexLocker(self._write_mutex)
        conn = self._get_connection()
        try:
            yield conn
        finally:
            pass

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA cache_size=-2000")  # 2 MB (negative = KB)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def close_all(self):
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None
