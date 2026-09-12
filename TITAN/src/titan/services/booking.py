"""Booking orchestration — the one place the receptionist's booking flow lives.

Ties together the existing seams to book an appointment like a real hospital
front desk:

1. validate the department and doctor against the :mod:`directory`,
2. check the calendar (Google Calendar when configured — the schedule source of
   truth) and, if the slot is taken, offer nearby alternatives,
3. create the calendar event,
4. persist the record to SQLite (primary datastore) and append it to the Excel
   report (best-effort).

Callers (the LLM tools) depend only on :func:`create_booking`, the :class:`Booking`
model, and :class:`SlotUnavailableError`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from titan.config import Settings, get_settings
from titan.services.appointment_db import get_appointment_db
from titan.services.calendar import (
    Appointment,
    AppointmentError,
    find_available_slots,
    get_calendar,
    slot_is_free,
)
from titan.services.directory import get_directory
from titan.services.reporting import get_report
from titan.services.tenant import get_business_context, load_holidays
from titan.utils import get_logger

logger = get_logger("titan.booking")


class SlotUnavailableError(AppointmentError):
    """Raised when the requested slot is taken; carries suggested alternatives."""

    def __init__(self, message: str, alternatives: list[datetime]) -> None:
        super().__init__(message)
        self.alternatives = alternatives


class Booking(BaseModel):
    """A confirmed appointment plus the patient details we persist."""

    name: str
    phone: str | None = None
    age: int | None = None
    gender: str | None = None
    department: str
    doctor: str
    symptoms: str | None = None
    start: datetime
    duration_minutes: int
    event_id: str | None = None
    html_link: str | None = None
    status: str = "confirmed"
    created_at: datetime | None = None

    @property
    def end(self) -> datetime:
        """The appointment's end time."""
        return self.start + timedelta(minutes=self.duration_minutes)


def _parse_when(
    preferred_date: str, preferred_time: str, settings: Settings
) -> datetime:
    """Combine an ISO date (YYYY-MM-DD) and time (HH:MM) into a tz-aware datetime."""
    raw = f"{preferred_date.strip()}T{preferred_time.strip()}"
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AppointmentError(
            f"I couldn't understand the date and time "
            f"{preferred_date!r} {preferred_time!r}. Please give a date like "
            f"2026-07-08 and a time like 15:00."
        ) from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo(settings.business_timezone))
    return when


def _event_notes(
    department: str,
    doctor: str,
    symptoms: str | None,
    age: int | None,
    gender: str | None,
) -> str:
    """Build a human-readable note stored on the calendar event."""
    parts = [f"Department: {department}", f"Doctor: {doctor}"]
    if symptoms:
        parts.append(f"Symptoms: {symptoms}")
    if age is not None:
        parts.append(f"Age: {age}")
    if gender:
        parts.append(f"Gender: {gender}")
    return " | ".join(parts)


def tenant_booking_env(
    settings: Settings | None = None,
) -> tuple[Settings, set, int | None]:
    """Resolve the effective ``(settings, holidays, business_id)`` for booking.

    With a tenant context (BUSINESS_SLUG set and found), the returned settings
    copy carries the business's timezone and hours and ``holidays`` its closed
    dates; otherwise the static settings pass through with no holidays. Shared
    by :func:`create_booking` and the availability tools so hours checks never
    disagree.
    """
    settings = settings or get_settings()
    context = get_business_context()
    if context is None:
        return settings, set(), None
    holidays = load_holidays(context.business_id)
    settings = settings.model_copy(
        update={
            "business_timezone": context.timezone,
            "business_hours_start": context.business_hours[0],
            "business_hours_end": context.business_hours[1],
        }
    )
    return settings, holidays, context.business_id


def create_booking(
    *,
    name: str,
    department: str,
    doctor: str,
    preferred_date: str,
    preferred_time: str,
    phone: str | None = None,
    age: int | None = None,
    gender: str | None = None,
    symptoms: str | None = None,
    duration_minutes: int | None = None,
    settings: Settings | None = None,
) -> Booking:
    """Validate, schedule, and persist an appointment.

    Raises :class:`SlotUnavailableError` (with ``alternatives``) if the requested time
    is taken, or :class:`AppointmentError` for an unknown department/doctor or an
    unparseable/out-of-hours time.
    """
    settings = settings or get_settings()

    # Per-business tenant overrides (dashboard-managed): hours, timezone, and
    # holidays. With no context (single-tenant env setup) behavior is unchanged.
    settings, holidays, business_id = tenant_booking_env(settings)

    directory = get_directory(settings)

    dept = directory.get_department(department)
    if dept is None:
        available = ", ".join(directory.department_names())
        raise AppointmentError(
            f"We don't have a {department} department. We have: {available}."
        )

    doc = directory.find_doctor(dept.name, doctor)
    if doc is None:
        names = ", ".join(d.name for d in dept.doctors)
        raise AppointmentError(
            f"{doctor} isn't in {dept.name}. Those doctors are: {names}."
        )

    duration = duration_minutes or settings.default_appointment_minutes
    start = _parse_when(preferred_date, preferred_time, settings)
    calendar = get_calendar()

    if not slot_is_free(calendar, settings, start, duration, holidays=holidays):
        alternatives = find_available_slots(
            calendar, settings, start, duration, holidays=holidays
        )
        raise SlotUnavailableError(
            "That time isn't available.", alternatives=alternatives
        )

    appt = Appointment(
        name=name,
        start=start,
        duration_minutes=duration,
        phone=phone,
        notes=_event_notes(dept.name, doc.name, symptoms, age, gender),
    )
    booked = calendar.create_appointment(appt)

    now = datetime.now(ZoneInfo(settings.business_timezone))
    booking = Booking(
        name=name,
        phone=phone,
        age=age,
        gender=gender,
        department=dept.name,
        doctor=doc.name,
        symptoms=symptoms,
        start=booked.start,
        duration_minutes=duration,
        event_id=booked.event_id,
        html_link=booked.html_link,
        status="confirmed",
        created_at=now,
    )

    # SQLite is the primary datastore; the Excel append is best-effort reporting.
    get_appointment_db().save(booking, business_id=business_id)
    get_report().append(booking)
    logger.info("Booking confirmed: %s", booking)
    return booking
