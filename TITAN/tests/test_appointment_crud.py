"""Tests for multi-tenant appointment CRUD on AppointmentDB."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from titan.services.booking import Booking

TZ = ZoneInfo("Asia/Kolkata")


def _booking(name: str = "Asha", hour: int = 10) -> Booking:
    return Booking(
        name=name,
        phone="9999900000",
        age=30,
        gender="female",
        department="Cardiology",
        doctor="Dr. Mehta",
        symptoms="chest pain",
        start=datetime(2026, 7, 20, hour, 0, tzinfo=TZ),
        duration_minutes=30,
        status="confirmed",
        created_at=datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh AppointmentDB over a temp database with migrations applied."""
    from titan.db import connection as db_connection
    from titan.services.appointment_db import AppointmentDB

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
    store = AppointmentDB.for_dashboard()
    yield store
    db_connection._reset()


def test_save_with_business_id_and_get(db):
    row_id = db.save(_booking(), business_id=1)
    row = db.get(row_id, business_id=1)
    assert row is not None
    assert row["name"] == "Asha"
    assert row["business_id"] == 1


def test_get_is_tenant_scoped(db):
    row_id = db.save(_booking(), business_id=1)
    assert db.get(row_id, business_id=2) is None


def test_list_filters_by_tenant_status_and_search(db):
    db.save(_booking("Asha"), business_id=1)
    db.save(_booking("Vikram", hour=11), business_id=1)
    db.save(_booking("Meera"), business_id=2)

    page = db.list(business_id=1)
    assert page["total"] == 2

    q = db.list(business_id=1, q="vik")
    assert q["total"] == 1
    assert q["items"][0]["name"] == "Vikram"

    other = db.list(business_id=2)
    assert other["total"] == 1


def test_list_date_range_and_pagination(db):
    for i in range(5):
        db.save(_booking(f"P{i}", hour=9 + i), business_id=1)
    page = db.list(business_id=1, page=1, page_size=2)
    assert page["total"] == 5
    assert len(page["items"]) == 2

    start = datetime(2026, 7, 20, 11, 0, tzinfo=TZ)
    ranged = db.list(business_id=1, from_dt=start.isoformat())
    assert ranged["total"] == 3  # 11:00, 12:00, 13:00


def test_update_edits_fields_and_bumps_updated_at(db):
    row_id = db.save(_booking(), business_id=1)
    ok = db.update(row_id, business_id=1, fields={"phone": "8888800000"})
    assert ok
    row = db.get(row_id, business_id=1)
    assert row["phone"] == "8888800000"
    assert row["updated_at"]


def test_update_rejects_unknown_fields(db):
    row_id = db.save(_booking(), business_id=1)
    with pytest.raises(ValueError):
        db.update(row_id, business_id=1, fields={"event_id; DROP": "x"})


def test_cancel_sets_status_and_timestamp(db):
    row_id = db.save(_booking(), business_id=1)
    assert db.cancel(row_id, business_id=1)
    row = db.get(row_id, business_id=1)
    assert row["status"] == "cancelled"
    assert row["cancelled_at"]


def test_cancel_wrong_tenant_is_noop(db):
    row_id = db.save(_booking(), business_id=1)
    assert not db.cancel(row_id, business_id=2)
    assert db.get(row_id, business_id=1)["status"] == "confirmed"


def test_reschedule_updates_times_and_status(db):
    row_id = db.save(_booking(), business_id=1)
    new_start = datetime(2026, 7, 21, 15, 0, tzinfo=TZ)
    new_end = new_start + timedelta(minutes=30)
    assert db.reschedule(
        row_id,
        business_id=1,
        new_start=new_start.isoformat(),
        new_end=new_end.isoformat(),
    )
    row = db.get(row_id, business_id=1)
    assert row["appointment_start"] == new_start.isoformat()
    assert row["status"] == "rescheduled"


def test_legacy_save_without_business_id_still_works(db):
    """The voice agent's booking flow calls save(booking) — must not break."""
    row_id = db.save(_booking())
    row = db.get(row_id, business_id=None)
    assert row["name"] == "Asha"
    assert row["business_id"] is None
    assert db.all()  # legacy read API intact
