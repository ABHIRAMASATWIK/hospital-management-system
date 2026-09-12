"""Tests for the titan.db package: connection factory + migration runner."""

from __future__ import annotations

import sqlite3

import pytest

from titan.db import connection as db_connection
from titan.db.migrations import CURRENT_VERSION, run_migrations


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """Point the db package at a fresh temp database and reset its state."""
    path = tmp_path / "titan-test.db"
    monkeypatch.setattr(db_connection, "_db_path_override", path)
    db_connection._reset()
    yield path
    db_connection._reset()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_migrations_create_all_tables(db_path):
    conn = db_connection.connect()
    run_migrations(conn)
    tables = _table_names(conn)
    expected = {
        "schema_migrations",
        "businesses",
        "users",
        "ai_agent_settings",
        "departments",
        "doctors",
        "appointments",
        "calls",
        "holiday_schedule",
        "app_settings",
    }
    assert expected <= tables
    conn.close()


def test_migrations_are_idempotent(db_path):
    conn = db_connection.connect()
    run_migrations(conn)
    version_before = conn.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    run_migrations(conn)  # second run must be a no-op
    version_after = conn.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    assert version_before == version_after == CURRENT_VERSION
    conn.close()


def test_migration_alters_existing_appointments_without_data_loss(db_path):
    """A pre-dashboard appointments table gains new columns; rows survive."""
    # Simulate the legacy schema created by AppointmentDB before migrations.
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE appointments (
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
        """
    )
    legacy.execute(
        "INSERT INTO appointments (name, department, doctor, appointment_start, created_at)"
        " VALUES ('Asha', 'Cardiology', 'Dr. Mehta', '2026-07-10T10:00:00', '2026-07-09T09:00:00')"
    )
    legacy.commit()
    legacy.close()

    conn = db_connection.connect()
    run_migrations(conn)
    cols = _columns(conn, "appointments")
    assert {"business_id", "updated_at", "cancelled_at"} <= cols
    row = conn.execute("SELECT name, department FROM appointments").fetchone()
    assert (row["name"], row["department"]) == ("Asha", "Cardiology")
    conn.close()


def test_get_db_applies_migrations_and_is_singleton(db_path):
    conn1 = db_connection.get_db()
    conn2 = db_connection.get_db()
    assert conn1 is conn2
    assert "businesses" in _table_names(conn1)


def test_foreign_keys_enabled(db_path):
    conn = db_connection.get_db()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
