"""Unit tests for the function tools and their registry.

These exercise the pure implementation helpers so they run fully offline
(no model or network access required).
"""

from datetime import datetime

import pytest

from titan.config import Settings
from titan.services.calendar import AppointmentError, InMemoryCalendar
from titan.services.call_store import CallStore
from titan.services.directory import Department, Directory, Doctor
from titan.tools import get_tools, registry


def _calendar() -> InMemoryCalendar:
    """An in-memory calendar with fixed 9-18 business hours for tests."""
    return InMemoryCalendar(
        Settings(
            business_timezone="Asia/Kolkata",
            business_hours_start=9,
            business_hours_end=18,
            default_appointment_minutes=30,
        )
    )


def test_get_tools_returns_all_tools() -> None:
    """The registry exposes exactly the expected set of tools."""
    tools = get_tools()
    assert len(tools) == 8


def test_list_departments_impl(monkeypatch) -> None:
    directory = Directory(
        [
            Department(name="Cardiology", doctors=[Doctor(name="Dr. Rajesh")]),
            Department(name="ENT", doctors=[Doctor(name="Dr. Ravi")]),
        ]
    )
    monkeypatch.setattr(registry, "get_directory", lambda: directory)
    assert registry.list_departments_impl() == ["Cardiology", "ENT"]


def test_list_doctors_impl(monkeypatch) -> None:
    directory = Directory(
        [Department(name="Cardiology", doctors=[Doctor(name="Dr. Rajesh")])]
    )
    monkeypatch.setattr(registry, "get_directory", lambda: directory)
    assert registry.list_doctors_impl("cardiology") == ["Dr. Rajesh"]
    # Unknown department yields no doctors (the wrapper steers the caller).
    assert registry.list_doctors_impl("Oncology") == []


def test_format_current_time_includes_timezone() -> None:
    """A valid timezone is reflected in the formatted output."""
    result = registry.format_current_time("UTC")
    assert "(UTC)" in result


def test_format_current_time_falls_back_to_utc() -> None:
    """An invalid timezone falls back to UTC instead of raising."""
    result = registry.format_current_time("Not/AZone")
    assert "(UTC)" in result


def test_record_lead_persists_to_store(monkeypatch) -> None:
    """record_lead stores the lead in the call store."""
    store = CallStore()
    monkeypatch.setattr(registry, "get_call_store", lambda: store)

    lead = registry.record_lead(name="Asha", phone="9876543210", notes="Wants a demo")

    assert lead.name == "Asha"
    # The lead is captured and attached to the next saved summary.
    from titan.services.call_store import CallSummary

    record = store.save_summary("call-1", CallSummary(intent="demo", summary="ok"))
    assert record.leads == [lead]


def test_schedule_appointment_books_via_calendar(monkeypatch) -> None:
    """schedule_appointment books through the configured calendar backend."""
    cal = _calendar()
    monkeypatch.setattr(registry, "get_calendar", lambda: cal)

    appt = registry.schedule_appointment(
        name="Asha", start_iso="2026-07-08T15:00", duration_minutes=30
    )

    assert appt.event_id is not None
    assert appt.name == "Asha"
    assert cal.list_appointments(datetime(2026, 7, 8).date()) == [appt]


def test_schedule_appointment_rejects_out_of_hours(monkeypatch) -> None:
    """A time outside business hours raises AppointmentError."""
    monkeypatch.setattr(registry, "get_calendar", _calendar)

    with pytest.raises(AppointmentError):
        registry.schedule_appointment(
            name="Night", start_iso="2026-07-08T03:00", duration_minutes=30
        )


def test_schedule_appointment_rejects_bad_datetime(monkeypatch) -> None:
    """An unparseable start time raises AppointmentError."""
    monkeypatch.setattr(registry, "get_calendar", _calendar)

    with pytest.raises(AppointmentError):
        registry.schedule_appointment(
            name="Oops", start_iso="next tuesday", duration_minutes=30
        )


def test_is_slot_available_reflects_bookings(monkeypatch) -> None:
    """is_slot_available is True for a free slot and False once it's taken."""
    cal = _calendar()
    monkeypatch.setattr(registry, "get_calendar", lambda: cal)

    assert registry.is_slot_available("2026-07-08T15:00", 30) is True
    registry.schedule_appointment(
        name="Asha", start_iso="2026-07-08T15:00", duration_minutes=30
    )
    assert registry.is_slot_available("2026-07-08T15:15", 30) is False
