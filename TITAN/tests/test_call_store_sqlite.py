"""Tests for the SQLite-backed call store (dashboard persistence)."""

from __future__ import annotations

import pytest

from titan.services.call_store import CallSummary, Lead


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from titan.db import connection as db_connection
    from titan.services.call_store import SqliteCallStore

    monkeypatch.setattr(db_connection, "_db_path_override", tmp_path / "t.db")
    db_connection._reset()
    conn = db_connection.get_db()
    now = "2026-07-15T00:00:00+00:00"
    conn.executemany(
        "INSERT INTO businesses (id, name, slug, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alpha Hospital", "alpha", now, now),
            (2, "Beta Clinic", "beta", now, now),
        ],
    )
    conn.commit()
    yield SqliteCallStore()
    db_connection._reset()


def _summary() -> CallSummary:
    return CallSummary(
        intent="book appointment",
        summary="Caller booked a cardiology appointment.",
        key_points=["cardiology", "Dr. Mehta"],
        action_items=["send confirmation"],
        sentiment="positive",
        follow_up_required=False,
    )


def test_summary_survives_a_new_store_instance(store):
    from titan.services.call_store import SqliteCallStore

    store.save_summary("call-1", _summary(), room="room-1")
    fresh = SqliteCallStore()  # same DB file, new instance = "process restart"
    record = fresh.get("call-1")
    assert record is not None
    assert record.summary.intent == "book appointment"
    assert record.room == "room-1"


def test_pending_leads_attach_to_next_summary(store):
    store.add_lead(Lead(name="Asha", phone="9999900000"))
    record = store.save_summary("call-2", _summary())
    assert [lead.name for lead in record.leads] == ["Asha"]
    # Pending buffer cleared: next call gets no stale leads.
    record2 = store.save_summary("call-3", _summary())
    assert record2.leads == []


def test_record_call_lifecycle_and_missed_status(store):
    store.record_call_lifecycle(
        "call-4",
        business_id=1,
        started_at="2026-07-15T10:00:00+05:30",
        ended_at="2026-07-15T10:00:05+05:30",
        status="missed",
    )
    rows = store.list(business_id=1)
    assert rows["total"] == 1
    assert rows["items"][0]["status"] == "missed"
    assert rows["items"][0]["duration_seconds"] == 5


def test_list_is_tenant_scoped_with_filters(store):
    store.save_summary("call-5", _summary(), business_id=1)
    store.save_summary("call-6", _summary(), business_id=2)
    assert store.list(business_id=1)["total"] == 1
    assert store.list(business_id=1, q="cardiology")["total"] == 1
    assert store.list(business_id=1, q="dermatology")["total"] == 0


def test_all_returns_records_like_the_memory_store(store):
    store.save_summary("call-7", _summary())
    records = store.all()
    assert len(records) == 1
    assert records[0].call_id == "call-7"
    assert records[0].summary.sentiment == "positive"
