"""SQLite access: thread-local connections, WAL, migrations, attached index DB."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .migrations import IDX_MIGRATIONS, MAIN_MIGRATIONS

log = logging.getLogger(__name__)


def _split(script: str) -> list[str]:
    """Split a script into statements, keeping CREATE TRIGGER bodies whole."""
    stmts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        buf.append(line)
        joined = "\n".join(buf).strip()
        if joined.endswith(";") and sqlite3.complete_statement(joined):
            stmts.append(joined)
            buf = []
    rest = "\n".join(buf).strip()
    if rest:
        stmts.append(rest)
    return stmts


def _migrate(conn: sqlite3.Connection, schema: str, migrations: list[str]) -> None:
    ver = conn.execute(f"PRAGMA {schema}.user_version").fetchone()[0]
    for i in range(ver, len(migrations)):
        log.info("Applying %s migration %d", schema, i + 1)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split(migrations[i]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA {schema}.user_version = {i + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


class Database:
    """Owns the file locations; hands out one connection per thread.

    The text/FTS index lives in a separate file attached as schema ``idx``.
    Table names are unique across both files so SQL can stay unqualified.
    """

    def __init__(self, db_path: Path, index_path: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.index_path = Path(index_path) if index_path else self.db_path.with_name("index.db")
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        c = self._open()
        self._local.conn = c
        _migrate(c, "main", MAIN_MIGRATIONS)
        _migrate(c, "idx", IDX_MIGRATIONS)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("ATTACH DATABASE ? AS idx", (str(self.index_path),))
        conn.execute("PRAGMA idx.journal_mode = WAL")
        conn.execute("PRAGMA idx.synchronous = NORMAL")
        with self._lock:
            self._conns.append(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._local.conn = self._open()
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Write transaction (BEGIN IMMEDIATE) on the calling thread's connection."""
        c = self.conn
        if c.in_transaction:  # nested: join the outer transaction
            yield c
            return
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            conns, self._conns = self._conns, []
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    # small helpers -------------------------------------------------------
    def get_state(self, key: str, default: str = "") -> str:
        r = self.conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_state(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO app_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
