"""Tests for the idempotent database seed script."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from titan.db import connection as db_connection

    monkeypatch.setattr(db_connection, "_db_path_override", tmp_path / "t.db")
    db_connection._reset()
    monkeypatch.setenv("SEED_ADMIN_EMAIL", "admin@abi.test")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "super-secret")

    doctors = tmp_path / "doctors.json"
    doctors.write_text(
        json.dumps(
            {
                "departments": [
                    {"name": "Cardiology", "doctors": [{"name": "Dr. Rajesh"}]},
                    {"name": "ENT", "doctors": [{"name": "Dr. Ravi"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCTORS_FILE", str(doctors))
    from titan.config import get_settings

    get_settings.cache_clear()
    yield db_connection.get_db()
    get_settings.cache_clear()
    db_connection._reset()


def _run_seed():
    from titan.db.seed import run_seed

    return run_seed()


def test_seed_creates_admin_business_and_directory(db):
    _run_seed()
    admin = db.execute(
        "SELECT role, business_id FROM users WHERE email = 'admin@abi.test'"
    ).fetchone()
    assert admin["role"] == "super_admin"
    assert admin["business_id"] is None

    business = db.execute(
        "SELECT * FROM businesses WHERE slug = 'abc-hospital'"
    ).fetchone()
    assert business is not None

    biz_admin = db.execute(
        "SELECT role FROM users WHERE business_id = ?", (business["id"],)
    ).fetchone()
    assert biz_admin["role"] == "business_admin"

    settings_row = db.execute(
        "SELECT assistant_name FROM ai_agent_settings WHERE business_id = ?",
        (business["id"],),
    ).fetchone()
    assert settings_row is not None

    assert db.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 2


def test_seed_is_idempotent(db):
    _run_seed()
    counts_before = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("users", "businesses", "departments", "doctors")
    }
    _run_seed()
    counts_after = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("users", "businesses", "departments", "doctors")
    }
    assert counts_before == counts_after


def test_seed_backfills_orphan_appointments(db):
    db.execute(
        "INSERT INTO appointments (name, department, doctor, appointment_start, created_at)"
        " VALUES ('Asha', 'Cardiology', 'Dr. Rajesh', '2026-07-20T10:00:00', '')"
    )
    db.commit()
    _run_seed()
    row = db.execute("SELECT business_id FROM appointments").fetchone()
    business = db.execute(
        "SELECT id FROM businesses WHERE slug='abc-hospital'"
    ).fetchone()
    assert row["business_id"] == business["id"]


def test_seeded_admin_password_verifies(db):
    _run_seed()
    from titan.api.auth import verify_password

    row = db.execute(
        "SELECT password_hash FROM users WHERE email = 'admin@abi.test'"
    ).fetchone()
    assert verify_password("super-secret", row["password_hash"])
    assert not verify_password("wrong", row["password_hash"])
