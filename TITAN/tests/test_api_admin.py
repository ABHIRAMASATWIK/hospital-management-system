"""Super-admin API tests: businesses, users, stats, status, logs, usage."""

from __future__ import annotations


def test_list_businesses(client, admin_headers):
    resp = client.get("/api/v1/admin/businesses", headers=admin_headers)
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["items"]}
    assert {"alpha", "beta"} <= slugs


def test_create_business_with_admin_and_settings(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/businesses",
        headers=admin_headers,
        json={"name": "Gamma Care", "slug": "gamma", "timezone": "Asia/Kolkata"},
    )
    assert resp.status_code == 201
    business_id = resp.json()["id"]
    # A default ai_agent_settings row must exist so the agent can boot.
    settings = client.get(
        "/api/v1/ai-settings",
        headers=admin_headers,
        params={"business_id": business_id},
    )
    assert settings.status_code == 200


def test_duplicate_slug_is_409(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/businesses",
        headers=admin_headers,
        json={"name": "Alpha Again", "slug": "alpha"},
    )
    assert resp.status_code == 409


def test_suspend_business(client, admin_headers):
    resp = client.patch(
        "/api/v1/admin/businesses/1",
        headers=admin_headers,
        json={"status": "suspended"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


def test_create_business_user(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "email": "reception@alpha.test",
            "password": "welcome123",
            "full_name": "Alpha Reception",
            "role": "business_admin",
            "business_id": 1,
        },
    )
    assert resp.status_code == 201
    # The new user can log in.
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "reception@alpha.test", "password": "welcome123"},
    )
    assert login.status_code == 200


def test_duplicate_email_is_409(client, admin_headers):
    payload = {
        "email": "alpha@biz.test",
        "password": "x12345678",
        "full_name": "Dup",
        "role": "business_admin",
        "business_id": 1,
    }
    assert (
        client.post(
            "/api/v1/admin/users", headers=admin_headers, json=payload
        ).status_code
        == 409
    )


def test_deactivate_user(client, admin_headers, api_env):
    user_id = api_env.execute(
        "SELECT id FROM users WHERE email = 'beta@biz.test'"
    ).fetchone()["id"]
    resp = client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert resp.status_code == 200
    login = client.post(
        "/api/v1/auth/login", json={"email": "beta@biz.test", "password": "pass1234"}
    )
    assert login.status_code == 401


def test_stats_status_logs_usage_shapes(client, admin_headers, api_env):
    api_env.execute(
        "INSERT INTO calls (call_id, business_id, status, duration_seconds, created_at)"
        " VALUES ('c-1', 1, 'completed', 300, '2026-07-14T10:00:00+05:30')"
    )
    api_env.commit()

    stats = client.get("/api/v1/admin/stats", headers=admin_headers)
    assert stats.status_code == 200
    assert {
        "total_businesses",
        "total_calls",
        "total_appointments",
        "per_business",
    } <= set(stats.json())

    status = client.get("/api/v1/admin/status", headers=admin_headers)
    assert status.status_code == 200
    assert status.json()["database"] == "ok"

    logs = client.get("/api/v1/admin/logs", headers=admin_headers)
    assert logs.status_code == 200
    assert logs.json()["total"] == 1

    usage = client.get("/api/v1/admin/usage", headers=admin_headers)
    assert usage.status_code == 200
    alpha_usage = next(u for u in usage.json() if u["business_id"] == 1)
    assert alpha_usage["call_minutes"] == 5.0


def test_app_settings_roundtrip(client, admin_headers):
    resp = client.patch(
        "/api/v1/admin/settings",
        headers=admin_headers,
        json={"support_email": "help@abi.test"},
    )
    assert resp.status_code == 200
    got = client.get("/api/v1/admin/settings", headers=admin_headers)
    assert got.json()["support_email"] == "help@abi.test"
