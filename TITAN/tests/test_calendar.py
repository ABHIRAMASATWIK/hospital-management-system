"""Offline unit tests for the in-memory calendar backend.

These exercise business-hours and overlap validation without any network or
credentials, using an :class:`InMemoryCalendar` with an explicit timezone.
"""

from datetime import date, datetime

import pytest

from titan.config import Settings
from titan.services.calendar import (
    Appointment,
    AppointmentError,
    InMemoryCalendar,
    find_available_slots,
    slot_is_free,
)


def _settings() -> Settings:
    """Deterministic 9-18 business hours in a fixed timezone."""
    return Settings(
        business_timezone="Asia/Kolkata",
        business_hours_start=9,
        business_hours_end=18,
        default_appointment_minutes=30,
        alternative_slot_count=3,
        alternative_search_window_minutes=180,
    )


def _calendar() -> InMemoryCalendar:
    """A calendar with deterministic 9-18 business hours in a fixed timezone."""
    return InMemoryCalendar(_settings())


def test_books_a_valid_slot() -> None:
    cal = _calendar()
    appt = cal.create_appointment(
        Appointment(name="Asha", start=datetime(2026, 7, 8, 15, 0), duration_minutes=30)
    )
    assert appt.event_id is not None
    assert appt.start.tzinfo is not None  # naive input becomes tz-aware
    assert cal.list_appointments(date(2026, 7, 8)) == [appt]


def test_rejects_out_of_hours() -> None:
    cal = _calendar()
    with pytest.raises(AppointmentError):
        cal.create_appointment(
            Appointment(
                name="Night Owl",
                start=datetime(2026, 7, 8, 3, 0),
                duration_minutes=30,
            )
        )


def test_rejects_appointment_running_past_close() -> None:
    cal = _calendar()
    # Starts at 17:45, a 30-min slot ends 18:15 — past the 18:00 close.
    with pytest.raises(AppointmentError):
        cal.create_appointment(
            Appointment(
                name="Late", start=datetime(2026, 7, 8, 17, 45), duration_minutes=30
            )
        )


def test_rejects_overlapping_slot() -> None:
    cal = _calendar()
    cal.create_appointment(
        Appointment(
            name="First", start=datetime(2026, 7, 8, 15, 0), duration_minutes=30
        )
    )
    with pytest.raises(AppointmentError):
        cal.create_appointment(
            Appointment(
                name="Clash", start=datetime(2026, 7, 8, 15, 15), duration_minutes=30
            )
        )


def test_adjacent_slots_do_not_conflict() -> None:
    cal = _calendar()
    cal.create_appointment(
        Appointment(
            name="First", start=datetime(2026, 7, 8, 15, 0), duration_minutes=30
        )
    )
    # Back-to-back at 15:30 is fine (end is exclusive).
    second = cal.create_appointment(
        Appointment(
            name="Second", start=datetime(2026, 7, 8, 15, 30), duration_minutes=30
        )
    )
    assert second.event_id is not None
    assert len(cal.list_appointments(date(2026, 7, 8))) == 2


def test_list_appointments_filters_by_day() -> None:
    cal = _calendar()
    cal.create_appointment(
        Appointment(
            name="Today", start=datetime(2026, 7, 8, 10, 0), duration_minutes=30
        )
    )
    assert cal.list_appointments(date(2026, 7, 9)) == []


def test_slot_is_free_reflects_bookings_and_hours() -> None:
    cal = _calendar()
    settings = _settings()
    start = datetime(2026, 7, 8, 15, 0)
    assert slot_is_free(cal, settings, start, 30) is True
    cal.create_appointment(Appointment(name="x", start=start, duration_minutes=30))
    assert slot_is_free(cal, settings, start, 30) is False
    # Out of hours is never free.
    assert slot_is_free(cal, settings, datetime(2026, 7, 8, 3, 0), 30) is False


def test_slot_is_free_respects_holidays() -> None:
    cal = _calendar()
    settings = _settings()
    start = datetime(2026, 7, 8, 15, 0)
    holidays = {date(2026, 7, 8)}
    assert slot_is_free(cal, settings, start, 30, holidays=holidays) is False
    # A non-holiday day is unaffected.
    assert (
        slot_is_free(cal, settings, datetime(2026, 7, 9, 15, 0), 30, holidays=holidays)
        is True
    )


def test_find_available_slots_skips_holiday() -> None:
    cal = _calendar()
    settings = _settings()
    around = datetime(2026, 7, 8, 15, 0)
    assert (
        find_available_slots(cal, settings, around, 30, holidays={date(2026, 7, 8)})
        == []
    )


def test_find_available_slots_returns_free_nearby() -> None:
    cal = _calendar()
    settings = _settings()
    taken = datetime(2026, 7, 8, 15, 0)
    cal.create_appointment(Appointment(name="x", start=taken, duration_minutes=30))

    slots = find_available_slots(cal, settings, taken, 30, count=3)

    assert len(slots) == 3
    # The taken slot itself is never suggested, and every suggestion is free.
    assert all(s.strftime("%H:%M") != "15:00" for s in slots)
    assert all(slot_is_free(cal, settings, s, 30) for s in slots)
