"""Auth endpoint tests: login, me, bad credentials, token validation."""

from __future__ import annotations

from conftest import login


def test_login_returns_token_and_user(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alpha@biz.test", "password": "pass1234"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["role"] == "business_admin"
    assert body["user"]["business_id"] == 1


def test_login_wrong_password_is_401(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alpha@biz.test", "password": "nope"},
    )
    assert resp.status_code == 401


def test_login_unknown_email_is_401(client):
    resp = client.post(
        "/api/v1/auth/login", json={"email": "ghost@biz.test", "password": "x"}
    )
    assert resp.status_code == 401


def test_me_roundtrip(client):
    headers = login(client, "root@abi.test")
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "super_admin"


def test_protected_route_without_token_is_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_garbage_token_is_401(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_inactive_user_cannot_use_token(client, api_env):
    headers = login(client, "alpha@biz.test")
    api_env.execute("UPDATE users SET is_active = 0 WHERE email = 'alpha@biz.test'")
    api_env.commit()
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
