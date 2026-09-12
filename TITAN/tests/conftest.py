"""Shared fixtures for the dashboard API tests.

``api_env`` gives each test a fresh temp database (migrations applied), two
seeded businesses with one business_admin each, plus a super admin — the
minimum world needed to exercise tenancy. ``client`` wraps the FastAPI app in
a TestClient bound to that database.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    from titan.db import connection as db_connection

    monkeypatch.setattr(db_connection, "_db_path_override", tmp_path / "api.db")
    db_connection._reset()

    from titan.api.auth import hash_password

    conn = db_connection.get_db()
    now = "2026-07-15T00:00:00+00:00"
    conn.executemany(
        "INSERT INTO businesses (id, name, slug, timezone, business_hours_start,"
        " business_hours_end, status, created_at, updated_at)"
        " VALUES (?, ?, ?, 'Asia/Kolkata', 9, 18, 'active', ?, ?)",
        [
            (1, "Alpha Hospital", "alpha", now, now),
            (2, "Beta Clinic", "beta", now, now),
        ],
    )
    pw = hash_password("pass1234")
    conn.executemany(
        "INSERT INTO users (email, password_hash, full_name, role, business_id,"
        " is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        [
            ("root@abi.test", pw, "Root", "super_admin", None, now, now),
            ("alpha@biz.test", pw, "Alpha Admin", "business_admin", 1, now, now),
            ("beta@biz.test", pw, "Beta Admin", "business_admin", 2, now, now),
        ],
    )
    conn.executemany(
        "INSERT INTO ai_agent_settings (business_id, assistant_name, voice,"
        " language, updated_at) VALUES (?, 'TITAN', 'pooja', 'hi-IN', ?)",
        [(1, now), (2, now)],
    )
    conn.commit()
    yield conn
    db_connection._reset()


@pytest.fixture()
def client(api_env):
    from fastapi.testclient import TestClient

    from titan.api.app import create_app

    return TestClient(create_app())


def login(client, email: str, password: str = "pass1234") -> dict:
    """Log in and return Authorization headers for subsequent requests."""
    resp = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def admin_headers(client):
    return login(client, "root@abi.test")


@pytest.fixture()
def alpha_headers(client):
    return login(client, "alpha@biz.test")


@pytest.fixture()
def beta_headers(client):
    return login(client, "beta@biz.test")
