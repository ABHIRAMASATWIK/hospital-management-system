"""Shared SQLite access for the TITAN dashboard data layer.

- :mod:`titan.db.connection` — connection factory (WAL, foreign keys, row
  factory) and the process-wide connection used by repositories.
- :mod:`titan.db.migrations` — ordered, idempotent schema migrations.

All SQL in this package sticks to portable constructs (TEXT/INTEGER columns,
ISO-8601 TEXT timestamps, ``?`` placeholders) so the storage layer can be moved
to a production database (e.g. Postgres) without rewriting callers.
"""

from titan.db.connection import connect, get_db
from titan.db.migrations import run_migrations

__all__ = ["connect", "get_db", "run_migrations"]
