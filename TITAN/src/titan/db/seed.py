"""Idempotent database seed for the TITAN dashboard.

Run with::

    uv run --no-sync python -m titan.db.seed

Creates (upserting by unique keys, so re-runs are safe):

- the ABI super admin (``SEED_ADMIN_EMAIL`` / ``SEED_ADMIN_PASSWORD`` env),
- a demo business ("ABC Hospital", slug ``abc-hospital``) with a
  business_admin user and default AI agent settings,
- departments/doctors migrated from the JSON directory file,
- ``business_id`` backfill for any pre-dashboard appointment/call rows.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from titan.config import get_settings
from titan.db import connection as db_connection
from titan.services.directory import Directory
from titan.utils import get_logger

logger = get_logger("titan.db.seed")

DEMO_BUSINESS = {
    "name": "ABC Hospital",
    "slug": "abc-hospital",
    "address": "MG Road, Bengaluru",
    "phone": "+91 80 0000 0000",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _upsert_user(
    conn,
    *,
    email: str,
    password: str,
    full_name: str,
    role: str,
    business_id: int | None,
) -> None:
    from titan.api.auth import hash_password

    exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO users (email, password_hash, full_name, role, business_id,"
        " is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (email, hash_password(password), full_name, role, business_id, _now(), _now()),
    )
    logger.info("Created %s user %s", role, email)


def run_seed() -> None:
    """Apply the full seed (idempotent)."""
    settings = get_settings()
    conn = db_connection.get_db()

    # 1. Super admin.
    admin_email = os.environ.get("SEED_ADMIN_EMAIL", "admin@abiautomations.com")
    admin_password = os.environ.get("SEED_ADMIN_PASSWORD", "change-me-now")
    _upsert_user(
        conn,
        email=admin_email,
        password=admin_password,
        full_name="ABI Super Admin",
        role="super_admin",
        business_id=None,
    )

    # 2. Demo business.
    row = conn.execute(
        "SELECT id FROM businesses WHERE slug = ?", (DEMO_BUSINESS["slug"],)
    ).fetchone()
    if row:
        business_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO businesses (name, slug, address, phone, timezone,"
            " business_hours_start, business_hours_end, status, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                DEMO_BUSINESS["name"],
                DEMO_BUSINESS["slug"],
                DEMO_BUSINESS["address"],
                DEMO_BUSINESS["phone"],
                settings.business_timezone,
                settings.business_hours_start,
                settings.business_hours_end,
                _now(),
                _now(),
            ),
        )
        business_id = int(cur.lastrowid)
        logger.info("Created demo business #%d %s", business_id, DEMO_BUSINESS["name"])

    # 3. Business admin + default AI settings.
    _upsert_user(
        conn,
        email=f"admin@{DEMO_BUSINESS['slug']}.test",
        password=os.environ.get("SEED_BUSINESS_PASSWORD", "change-me-too"),
        full_name=f"{DEMO_BUSINESS['name']} Admin",
        role="business_admin",
        business_id=business_id,
    )
    if not conn.execute(
        "SELECT id FROM ai_agent_settings WHERE business_id = ?", (business_id,)
    ).fetchone():
        conn.execute(
            "INSERT INTO ai_agent_settings (business_id, assistant_name, voice,"
            " language, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                business_id,
                settings.assistant_name,
                settings.tts_speaker,
                settings.tts_target_language,
                _now(),
            ),
        )

    # 4. Directory migration: doctors.json -> departments/doctors tables.
    if not conn.execute(
        "SELECT id FROM departments WHERE business_id = ?", (business_id,)
    ).fetchone():
        directory = Directory.from_file(settings.doctors_path)
        for dept in directory.list_departments():
            cur = conn.execute(
                "INSERT INTO departments (business_id, name) VALUES (?, ?)",
                (business_id, dept.name),
            )
            dept_id = int(cur.lastrowid)
            for doc in dept.doctors:
                conn.execute(
                    "INSERT INTO doctors (business_id, department_id, name,"
                    " specialization, is_active) VALUES (?, ?, ?, ?, 1)",
                    (business_id, dept_id, doc.name, doc.specialization),
                )
        logger.info("Migrated doctors.json into DB for business #%d", business_id)

    # 5. Backfill tenant on pre-dashboard rows.
    conn.execute(
        "UPDATE appointments SET business_id = ? WHERE business_id IS NULL",
        (business_id,),
    )
    conn.execute(
        "UPDATE calls SET business_id = ? WHERE business_id IS NULL", (business_id,)
    )
    conn.commit()
    logger.info("Seed complete.")


if __name__ == "__main__":
    run_seed()
