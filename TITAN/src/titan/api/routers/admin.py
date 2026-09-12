"""Super-admin endpoints: client/business management, users, fleet stats."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from titan import __version__
from titan.api.auth import hash_password, require_permission
from titan.api.permissions import PERMISSIONS
from titan.db import connection as db_connection

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_permission("admin:manage"))],
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


# --------------------------------------------------------------------------
# Businesses
# --------------------------------------------------------------------------


class BusinessCreate(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    address: str | None = None
    phone: str | None = None
    timezone: str = "Asia/Kolkata"
    business_hours_start: int = Field(default=9, ge=0, le=23)
    business_hours_end: int = Field(default=18, ge=1, le=24)


class BusinessUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    phone: str | None = None
    timezone: str | None = None
    business_hours_start: int | None = Field(default=None, ge=0, le=23)
    business_hours_end: int | None = Field(default=None, ge=1, le=24)
    status: str | None = Field(default=None, pattern=r"^(active|suspended)$")


@router.get("/businesses")
def list_businesses(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    conn = db_connection.get_db()
    total = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM businesses ORDER BY name LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/businesses", status_code=201)
def create_business(body: BusinessCreate) -> dict:
    conn = db_connection.get_db()
    now = _now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO businesses (name, slug, address, phone, timezone,"
            " business_hours_start, business_hours_end, status, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                body.name,
                body.slug,
                body.address,
                body.phone,
                body.timezone,
                body.business_hours_start,
                body.business_hours_end,
                now,
                now,
            ),
        )
        business_id = int(cur.lastrowid)
        # Default AI settings so the agent (and dashboard) work immediately.
        conn.execute(
            "INSERT INTO ai_agent_settings (business_id, updated_at) VALUES (?, ?)",
            (business_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Slug already in use.") from exc
    row = conn.execute(
        "SELECT * FROM businesses WHERE id = ?", (business_id,)
    ).fetchone()
    return dict(row)


@router.get("/businesses/{business_id}")
def get_business(business_id: int) -> dict:
    row = (
        db_connection.get_db()
        .execute("SELECT * FROM businesses WHERE id = ?", (business_id,))
        .fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Business not found.")
    return dict(row)


@router.patch("/businesses/{business_id}")
def update_business(business_id: int, body: BusinessUpdate) -> dict:
    fields = body.model_dump(exclude_none=True)
    if fields:
        conn = db_connection.get_db()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(
            f"UPDATE businesses SET {assignments}, updated_at = ? WHERE id = ?",
            [*fields.values(), _now_iso(), business_id],
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Business not found.")
    return get_business(business_id)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str
    role: str
    business_id: int | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: str | None = None
    business_id: int | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


@router.get("/users")
def list_users(business_id: int | None = None) -> dict:
    conn = db_connection.get_db()
    if business_id is not None:
        rows = conn.execute(
            "SELECT id, email, full_name, role, business_id, is_active, created_at"
            " FROM users WHERE business_id = ? ORDER BY email",
            (business_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, email, full_name, role, business_id, is_active, created_at"
            " FROM users ORDER BY email"
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post("/users", status_code=201)
def create_user(body: UserCreate) -> dict:
    if body.role not in PERMISSIONS:
        raise HTTPException(status_code=422, detail=f"Unknown role {body.role!r}.")
    if body.role != "super_admin" and body.business_id is None:
        raise HTTPException(
            status_code=422, detail="Business roles require a business_id."
        )
    conn = db_connection.get_db()
    now = _now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, full_name, role, business_id,"
            " is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (
                body.email.lower(),
                hash_password(body.password),
                body.full_name,
                body.role,
                body.business_id,
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Email already in use.") from exc
    return {
        "id": int(cur.lastrowid),
        "email": body.email.lower(),
        "full_name": body.full_name,
        "role": body.role,
        "business_id": body.business_id,
    }


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate) -> dict:
    fields = body.model_dump(exclude_none=True)
    if "password" in fields:
        fields["password_hash"] = hash_password(fields.pop("password"))
    if "is_active" in fields:
        fields["is_active"] = int(fields["is_active"])
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update.")
    conn = db_connection.get_db()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",
        [*fields.values(), _now_iso(), user_id],
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found.")
    row = conn.execute(
        "SELECT id, email, full_name, role, business_id, is_active FROM users"
        " WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row)


# --------------------------------------------------------------------------
# Fleet stats / status / logs / usage
# --------------------------------------------------------------------------


@router.get("/stats")
def stats() -> dict:
    conn = db_connection.get_db()

    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    per_business = conn.execute(
        """
        SELECT b.id AS business_id, b.name,
               (SELECT COUNT(*) FROM calls c WHERE c.business_id = b.id) AS calls,
               (SELECT COUNT(*) FROM calls c WHERE c.business_id = b.id
                 AND c.status = 'missed') AS missed_calls,
               (SELECT COUNT(*) FROM appointments a
                 WHERE a.business_id = b.id) AS appointments
        FROM businesses b ORDER BY b.name
        """
    ).fetchall()
    return {
        "total_businesses": one("SELECT COUNT(*) FROM businesses"),
        "total_calls": one("SELECT COUNT(*) FROM calls"),
        "total_appointments": one("SELECT COUNT(*) FROM appointments"),
        "per_business": [dict(r) for r in per_business],
    }


@router.get("/status")
def system_status() -> dict:
    conn = db_connection.get_db()
    try:
        conn.execute("SELECT 1")
        database = "ok"
    except sqlite3.Error:  # pragma: no cover
        database = "error"
    missing_settings = conn.execute(
        "SELECT b.id, b.name FROM businesses b"
        " LEFT JOIN ai_agent_settings s ON s.business_id = b.id WHERE s.id IS NULL"
    ).fetchall()
    return {
        "database": database,
        "version": __version__,
        "businesses_missing_ai_settings": [dict(r) for r in missing_settings],
    }


@router.get("/logs")
def ai_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict:
    conn = db_connection.get_db()
    total = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    rows = conn.execute(
        "SELECT c.*, b.name AS business_name FROM calls c"
        " LEFT JOIN businesses b ON b.id = c.business_id"
        " ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/usage")
def usage() -> list[dict]:
    rows = (
        db_connection.get_db()
        .execute(
            """
        SELECT b.id AS business_id, b.name,
               COUNT(c.id) AS call_count,
               ROUND(COALESCE(SUM(c.duration_seconds), 0) / 60.0, 1) AS call_minutes
        FROM businesses b
        LEFT JOIN calls c ON c.business_id = b.id
        GROUP BY b.id ORDER BY b.name
        """
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Global app settings (key/value)
# --------------------------------------------------------------------------


@router.get("/settings")
def get_app_settings() -> dict:
    rows = (
        db_connection.get_db().execute("SELECT key, value FROM app_settings").fetchall()
    )
    return {r["key"]: r["value"] for r in rows}


@router.patch("/settings")
def update_app_settings(body: dict[str, str]) -> dict:
    conn = db_connection.get_db()
    for key, value in body.items():
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()
    return get_app_settings()
