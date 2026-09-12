"""Ordered, idempotent schema migrations for the dashboard database.

A tiny hand-rolled runner (no Alembic): applied versions are recorded in
``schema_migrations`` and each migration runs at most once per database. All
SQL is portable (TEXT/INTEGER, ISO-8601 TEXT timestamps) so the schema can be
recreated on Postgres later with mechanical changes only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from titan.utils import get_logger

logger = get_logger("titan.db.migrations")

_MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    address TEXT,
    phone TEXT,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    business_hours_start INTEGER NOT NULL DEFAULT 9,
    business_hours_end INTEGER NOT NULL DEFAULT 18,
    working_days TEXT NOT NULL DEFAULT '[1,2,3,4,5,6]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    business_id INTEGER REFERENCES businesses(id),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_agent_settings (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL UNIQUE REFERENCES businesses(id),
    assistant_name TEXT NOT NULL DEFAULT 'TITAN',
    greeting TEXT,
    prompt_override TEXT,
    voice TEXT NOT NULL DEFAULT 'pooja',
    language TEXT NOT NULL DEFAULT 'hi-IN',
    call_flow_config TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    name TEXT NOT NULL,
    UNIQUE (business_id, name)
);

CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    name TEXT NOT NULL,
    specialization TEXT,
    availability TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    age INTEGER,
    gender TEXT,
    department TEXT NOT NULL,
    doctor TEXT NOT NULL,
    symptoms TEXT,
    appointment_start TEXT NOT NULL,
    appointment_end TEXT,
    event_id TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    business_id INTEGER REFERENCES businesses(id),
    room TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    started_at TEXT,
    ended_at TEXT,
    duration_seconds INTEGER,
    caller_phone TEXT,
    intent TEXT,
    summary TEXT,
    sentiment TEXT,
    key_points TEXT NOT NULL DEFAULT '[]',
    action_items TEXT NOT NULL DEFAULT '[]',
    follow_up_required INTEGER NOT NULL DEFAULT 0,
    leads TEXT NOT NULL DEFAULT '[]',
    extra TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holiday_schedule (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    date TEXT NOT NULL,
    label TEXT,
    UNIQUE (business_id, date)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_business_created
    ON calls (business_id, created_at);
CREATE INDEX IF NOT EXISTS idx_doctors_business ON doctors (business_id);
CREATE INDEX IF NOT EXISTS idx_users_business ON users (business_id);
"""

# Columns migration 001 adds to a pre-existing (voice-agent era) appointments
# table. On a fresh database the CREATE above already includes them via ALTER.
_APPOINTMENT_NEW_COLUMNS: dict[str, str] = {
    "business_id": "INTEGER REFERENCES businesses(id)",
    "updated_at": "TEXT",
    "cancelled_at": "TEXT",
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migration_001(conn: sqlite3.Connection) -> None:
    """Create all dashboard tables and extend the legacy appointments table."""
    conn.executescript(_MIGRATION_001)
    present = _existing_columns(conn, "appointments")
    for column, ddl in _APPOINTMENT_NEW_COLUMNS.items():
        if column not in present:
            conn.execute(f"ALTER TABLE appointments ADD COLUMN {column} {ddl}")
    # This index needs business_id, which the ALTERs above may have just added.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_appointments_business_start"
        " ON appointments (business_id, appointment_start)"
    )


# Columns migration 002 adds for dashboard payment tracking and meeting
# completion. TEXT with defaults keeps the SQL portable, per module docstring.
_APPOINTMENT_PAYMENT_COLUMNS: dict[str, str] = {
    "payment_status": "TEXT NOT NULL DEFAULT 'pending'",
    "payment_method": "TEXT",
    "payment_notes": "TEXT",
    "completed_at": "TEXT",
}


def _migration_002(conn: sqlite3.Connection) -> None:
    """Add payment tracking and completion timestamp to appointments."""
    present = _existing_columns(conn, "appointments")
    for column, ddl in _APPOINTMENT_PAYMENT_COLUMNS.items():
        if column not in present:
            conn.execute(f"ALTER TABLE appointments ADD COLUMN {column} {ddl}")


# Ordered list of (version, migration callable). Append-only.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001),
    (2, _migration_002),
]

CURRENT_VERSION = MIGRATIONS[-1][0]


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any not-yet-applied migrations to ``conn`` (idempotent)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, migrate in MIGRATIONS:
        if version in applied:
            continue
        migrate(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        logger.info("Applied schema migration %03d", version)
