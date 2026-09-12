"""Tests for per-business tenant context resolution (services/tenant.py)."""

from __future__ import annotations

import pytest

from titan.prompts import build_instructions


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    from titan.db import connection as db_connection

    monkeypatch.setattr(db_connection, "_db_path_override", tmp_path / "t.db")
    db_connection._reset()
    conn = db_connection.get_db()
    conn.execute(
        "INSERT INTO businesses (id, name, slug, timezone, business_hours_start,"
        " business_hours_end, status, created_at, updated_at)"
        " VALUES (1, 'ABC Hospital', 'abc-hospital', 'Asia/Kolkata', 8, 20,"
        " 'active', '', '')"
    )
    conn.execute(
        "INSERT INTO ai_agent_settings (business_id, assistant_name, greeting,"
        " prompt_override, voice, language, updated_at)"
        " VALUES (1, 'Nova', 'Namaste! ABC Hospital mein aapka swagat hai.',"
        " NULL, 'anushka', 'en-IN', '')"
    )
    conn.commit()
    yield conn
    db_connection._reset()


def test_get_business_context_loads_business_and_settings(seeded_db):
    from titan.services.tenant import get_business_context

    ctx = get_business_context("abc-hospital")
    assert ctx is not None
    assert ctx.business_id == 1
    assert ctx.business_name == "ABC Hospital"
    assert ctx.assistant_name == "Nova"
    assert ctx.greeting.startswith("Namaste")
    assert ctx.voice == "anushka"
    assert ctx.language == "en-IN"
    assert ctx.business_hours == (8, 20)


def test_unknown_slug_returns_none(seeded_db):
    from titan.services.tenant import get_business_context

    assert get_business_context("nope") is None


def test_context_reflects_dashboard_edits_on_reload(seeded_db):
    """Editing settings in the DB must show up on the next session load."""
    from titan.services.tenant import get_business_context

    seeded_db.execute(
        "UPDATE ai_agent_settings SET greeting = 'Hello from the dashboard'"
        " WHERE business_id = 1"
    )
    seeded_db.commit()
    ctx = get_business_context("abc-hospital")
    assert ctx.greeting == "Hello from the dashboard"


def test_build_instructions_greeting_and_override():
    base = build_instructions(
        assistant_name="Nova",
        user_name="Abhi",
        persona="receptionist",
        hospital_name="ABC Hospital",
        greeting="Custom greeting line.",
    )
    assert "Custom greeting line." in base

    overridden = build_instructions(
        assistant_name="Nova",
        user_name="Abhi",
        persona="receptionist",
        hospital_name="ABC Hospital",
        prompt_override="You are a completely custom persona.",
    )
    assert "You are a completely custom persona." in overridden
    # Voice output rules must survive a full prompt override.
    assert "voice" in overridden.lower()
