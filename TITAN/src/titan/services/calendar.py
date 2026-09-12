"""Calendar seam for booking appointments.

Like :mod:`titan.services.call_store`, this is a swappable seam: tools depend
only on :func:`get_calendar` and the :class:`Appointment` model, so a real
provider (Google Calendar) can replace the default in-memory calendar without
touching any caller.

The default :class:`InMemoryCalendar` validates business hours and rejects
overlapping bookings, so the booking flow is fully exercisable offline (no
network, no credentials) in tests and the console.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from threading import Lock
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from titan.config import Settings, get_settings
from titan.utils import get_logger

logger = get_logger("titan.calendar")


class AppointmentError(Exception):
    """Raised when a slot cannot be booked (out of hours, taken, or invalid)."""


class Appointment(BaseModel):
    """A single scheduled appointment."""

    name: str = Field(description="Who the appointment is with.")
    start: datetime = Field(description="Start time (tz-aware once booked).")
    duration_minutes: int = Field(gt=0)
    phone: str | None = None
    email: str | None = None
    notes: str | None = None
    # Populated by the backend once the event is created.
    event_id: str | None = None
    html_link: str | None = None

    @property
    def end(self) -> datetime:
        """The appointment's end time."""
        return self.start + timedelta(minutes=self.duration_minutes)


@runtime_checkable
class CalendarBackend(Protocol):
    """Interface every calendar implementation must satisfy."""

    def create_appointment(self, appt: Appointment) -> Appointment:
        """Book ``appt`` and return it with backend-assigned fields populated."""
        ...

    def list_appointments(self, day: date) -> list[Appointment]:
        """Return appointments scheduled on ``day``."""
        ...


def _ensure_aware(when: datetime, tz: ZoneInfo) -> datetime:
    """Attach ``tz`` to a naive datetime; leave aware datetimes untouched."""
    return when if when.tzinfo is not None else when.replace(tzinfo=tz)


def _overlaps(a_start: datetime, a_end: datetime, b: Appointment) -> bool:
    """True when interval [a_start, a_end) overlaps appointment ``b``."""
    return a_start < b.end and b.start < a_end


def _within_business_hours(start: datetime, end: datetime, settings: Settings) -> bool:
    """True when [start, end) fits inside the configured business hours.

    Shared by :class:`InMemoryCalendar` and the backend-agnostic availability
    helpers below so the "in hours" rule lives in exactly one place.
    """
    open_h = settings.business_hours_start
    close_h = settings.business_hours_end
    # Reject appointments that cross midnight or straddle the boundary.
    if start.date() != end.date():
        return False
    start_ok = start.hour >= open_h
    # end is exclusive: an appointment ending exactly at close_h:00 is fine.
    end_ok = (end.hour, end.minute, end.second) <= (close_h, 0, 0)
    return start_ok and end_ok


def _overlaps_any(
    start: datetime, duration_minutes: int, appts: list[Appointment], tz: ZoneInfo
) -> bool:
    """True when [start, start+duration) overlaps any of ``appts``."""
    end = start + timedelta(minutes=duration_minutes)
    for a in appts:
        a_start = _ensure_aware(a.start, tz)
        a_end = a_start + timedelta(minutes=a.duration_minutes)
        if start < a_end and a_start < end:
            return True
    return False


class InMemoryCalendar:
    """Thread-safe, process-local calendar used as the default backend.

    Enforces two rules so the booking flow behaves realistically offline:

    - the whole appointment must fall within configured business hours, and
    - it must not overlap an existing appointment.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tz = ZoneInfo(self._settings.business_timezone)
        self._appointments: list[Appointment] = []
        self._lock = Lock()
        self._counter = 0

    def _within_business_hours(self, start: datetime, end: datetime) -> bool:
        return _within_business_hours(start, end, self._settings)

    def create_appointment(self, appt: Appointment) -> Appointment:
        start = _ensure_aware(appt.start, self._tz)
        end = start + timedelta(minutes=appt.duration_minutes)

        if not self._within_business_hours(start, end):
            raise AppointmentError(
                f"Requested time is outside business hours "
                f"({self._settings.business_hours_start:02d}:00-"
                f"{self._settings.business_hours_end:02d}:00 "
                f"{self._settings.business_timezone})."
            )

        with self._lock:
            for existing in self._appointments:
                if _overlaps(start, end, existing):
                    raise AppointmentError(
                        "That time slot is already booked; please pick another."
                    )
            self._counter += 1
            booked = appt.model_copy(
                update={"start": start, "event_id": f"mem-{self._counter}"}
            )
            self._appointments.append(booked)
            logger.info("Booked appointment: %s", booked)
            return booked

    def list_appointments(self, day: date) -> list[Appointment]:
        with self._lock:
            return [a for a in self._appointments if a.start.date() == day]


def slot_is_free(
    backend: CalendarBackend,
    settings: Settings,
    start: datetime,
    duration_minutes: int,
    holidays: set[date] | None = None,
) -> bool:
    """True when the slot is within business hours and not already booked.

    Works against any :class:`CalendarBackend` via ``list_appointments``, so the
    same check applies to the in-memory calendar (tests/console) and Google
    Calendar (production source of truth) alike.

    ``holidays`` (per-business closed dates from the dashboard) are never free;
    omit it to keep the original hours-only behavior.
    """
    tz = ZoneInfo(settings.business_timezone)
    start = _ensure_aware(start, tz)
    if holidays and start.date() in holidays:
        return False
    end = start + timedelta(minutes=duration_minutes)
    if not _within_business_hours(start, end, settings):
        return False
    appts = backend.list_appointments(start.date())
    return not _overlaps_any(start, duration_minutes, appts, tz)


def _day_slots(
    day: date, duration_minutes: int, settings: Settings, tz: ZoneInfo
) -> list[datetime]:
    """Every in-hours start time on ``day``, stepping by the slot duration."""
    open_dt = datetime.combine(day, time(settings.business_hours_start, 0), tzinfo=tz)
    # Handle business_hours_end == 24 (midnight) without an invalid time(24, ...).
    close_dt = datetime.combine(day, time.min, tzinfo=tz) + timedelta(
        hours=settings.business_hours_end
    )
    step = timedelta(minutes=duration_minutes)
    slots: list[datetime] = []
    cur = open_dt
    while cur + step <= close_dt:
        slots.append(cur)
        cur += step
    return slots


def find_available_slots(
    backend: CalendarBackend,
    settings: Settings,
    around: datetime,
    duration_minutes: int,
    count: int | None = None,
    holidays: set[date] | None = None,
) -> list[datetime]:
    """Return up to ``count`` free in-hours slots nearest to ``around``.

    Candidates on the same business day are ranked by closeness to the requested
    time. Slots within ``alternative_search_window_minutes`` are preferred; the
    search widens to the whole day only if that window can't supply enough.
    Fetches the day's appointments once, so it stays cheap even against a remote
    calendar backend. A ``holidays`` date set (dashboard closed days) yields no
    slots on those days.
    """
    count = count or settings.alternative_slot_count
    window = timedelta(minutes=settings.alternative_search_window_minutes)
    tz = ZoneInfo(settings.business_timezone)
    around = _ensure_aware(around, tz)

    if holidays and around.date() in holidays:
        return []

    candidates = _day_slots(around.date(), duration_minutes, settings, tz)
    candidates.sort(key=lambda c: abs((c - around).total_seconds()))

    appts = backend.list_appointments(around.date())
    free = [
        c
        for c in candidates
        if c != around and not _overlaps_any(c, duration_minutes, appts, tz)
    ]
    within = [c for c in free if abs(c - around) <= window]
    chosen = within if len(within) >= count else free
    return chosen[:count]


_calendar: CalendarBackend | None = None


def get_calendar() -> CalendarBackend:
    """Return the process-wide calendar backend selected by settings.

    ``calendar_backend="memory"`` (default) uses :class:`InMemoryCalendar`;
    ``"google"`` lazily loads the Google Calendar backend so its extra
    dependencies stay optional.
    """
    global _calendar
    if _calendar is None:
        settings = get_settings()
        backend = settings.calendar_backend.lower()
        if backend == "google":
            # Imported lazily: the google-api-python-client extra is optional.
            from titan.services.google_calendar import GoogleCalendarBackend

            _calendar = GoogleCalendarBackend(settings)
        elif backend == "memory":
            _calendar = InMemoryCalendar(settings)
        else:
            raise ValueError(
                f"Unknown calendar_backend {backend!r}; expected 'memory' or 'google'."
            )
    return _calendar
