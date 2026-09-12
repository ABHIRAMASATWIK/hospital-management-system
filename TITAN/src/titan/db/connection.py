"""SQLite connection factory for the dashboard data layer.

Mirrors the existing per-seam style (:mod:`titan.services.appointment_db`) but
centralizes pragmas and migrations so every table lives in one schema-managed
database file (``settings.database_path``).

``get_db()`` returns a process-wide connection with migrations applied; tests
point the package at a temp file via ``_db_path_override`` and call
``_reset()`` between cases.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Lock

from titan.config import get_settings
from titan.utils import get_logger

logger = get_logger("titan.db")

# Test seam: when set, connections open this path instead of settings'.
_db_path_override: Path | None = None

_conn: sqlite3.Connection | None = None
_lock = Lock()


def _db_path() -> Path:
    return _db_path_override or get_settings().database_path


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a new connection with the standard pragmas applied.

    ``path`` overrides the configured database file (used by seams that accept
    their own :class:`~titan.config.Settings`, e.g. in tests).
    """
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    """Return the process-wide migrated connection (created lazily)."""
    global _conn
    with _lock:
        if _conn is None:
            from titan.db.migrations import run_migrations

            _conn = connect()
            run_migrations(_conn)
            logger.info("Dashboard database ready at %s", _db_path())
        return _conn


def _reset() -> None:
    """Close and forget the shared connection (test helper)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
