"""LLM-callable function tools and the registry that exposes them.

Each tool has two parts:

- a plain, unit-testable implementation helper (``*_impl`` / ``record_lead`` etc.),
- a thin ``@function_tool``-decorated wrapper the LLM actually calls.

Separating them keeps the business logic testable without constructing a live
``RunContext``. ``get_tools()`` returns the wrappers attached to the agent, so
adding, removing, or feature-flagging a tool happens in one place.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from livekit.agents import RunContext, function_tool

from titan.config import get_settings
from titan.services.booking import (
    SlotUnavailableError,
    create_booking,
    tenant_booking_env,
)
from titan.services.calendar import (
    Appointment,
    AppointmentError,
    find_available_slots,
    get_calendar,
    slot_is_free,
)
from titan.services.call_store import Lead, get_call_store
from titan.services.directory import get_directory
from titan.utils import get_logger

logger = get_logger("titan.tools")


# --- Implementation helpers (pure, testable) --------------------------------


def format_current_time(timezone: str = "Asia/Kolkata") -> str:
    """Return the current date/time in ``timezone`` as a spoken-friendly string.

    Falls back to UTC when the timezone name is invalid.
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        logger.warning("Unknown timezone %r; falling back to UTC", timezone)
        now = datetime.now(ZoneInfo("UTC"))
        timezone = "UTC"
    return now.strftime(f"%A, %d %B %Y, %I:%M %p ({timezone})")


def record_lead(
    name: str,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> Lead:
    """Create a :class:`Lead`, store it, and return it."""
    lead = Lead(name=name, phone=phone, email=email, notes=notes)
    get_call_store().add_lead(lead)
    logger.info("Captured lead: %s", lead)
    return lead


def _parse_start(start_iso: str) -> datetime:
    """Parse an ISO-8601 start time, raising :class:`AppointmentError` if invalid."""
    try:
        return datetime.fromisoformat(start_iso)
    except ValueError as exc:
        raise AppointmentError(
            f"Could not understand the date/time {start_iso!r}."
        ) from exc


def schedule_appointment(
    name: str,
    start_iso: str,
    duration_minutes: int | None = None,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> Appointment:
    """Validate and book an appointment via the configured calendar backend.

    ``start_iso`` is an ISO-8601 datetime; a naive value is interpreted in the
    configured business timezone. Raises :class:`AppointmentError` on a bad time
    or an unavailable slot.
    """
    duration = duration_minutes or get_settings().default_appointment_minutes
    appt = Appointment(
        name=name,
        start=_parse_start(start_iso),
        duration_minutes=duration,
        phone=phone,
        email=email,
        notes=notes,
    )
    booked = get_calendar().create_appointment(appt)
    logger.info("Scheduled appointment: %s", booked)
    return booked


def is_slot_available(start_iso: str, duration_minutes: int | None = None) -> bool:
    """Return True if the requested slot can be booked (in hours and free)."""
    settings, holidays, _ = tenant_booking_env()
    duration = duration_minutes or settings.default_appointment_minutes
    start = _parse_start(start_iso)
    return slot_is_free(get_calendar(), settings, start, duration, holidays=holidays)


def _spoken_time(when: datetime) -> str:
    """Format a datetime for natural, voice-friendly readback."""
    return when.strftime("%A, %d %B at %I:%M %p")


def list_departments_impl() -> list[str]:
    """Return the hospital's department names."""
    return get_directory().department_names()


def list_doctors_impl(department: str) -> list[str]:
    """Return the doctor names in ``department`` (empty if it's unknown)."""
    directory = get_directory()
    dept = directory.get_department(department)
    return [d.name for d in dept.doctors] if dept else []


def suggest_slots(
    start_iso: str, duration_minutes: int | None = None
) -> list[datetime]:
    """Return nearby available slots around ``start_iso`` (for offering alternatives)."""
    settings, holidays, _ = tenant_booking_env()
    duration = duration_minutes or settings.default_appointment_minutes
    start = _parse_start(start_iso)
    return find_available_slots(
        get_calendar(), settings, start, duration, holidays=holidays
    )


# --- Function tools (LLM-facing) --------------------------------------------


@function_tool
async def get_current_time(
    context: RunContext,
    timezone: str = "Asia/Kolkata",
) -> str:
    """Return the current date and time in the given timezone.

    Args:
        timezone: An IANA timezone name (e.g. "Asia/Kolkata", "UTC").
    """
    return format_current_time(timezone)


@function_tool
async def save_lead(
    context: RunContext,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> str:
    """Save a caller's contact details as a lead for follow-up.

    Use this when the caller shares their contact information or expresses
    interest in being contacted. This is the seam a CRM integration plugs into.

    Args:
        name: The caller's name.
        phone: The caller's phone number, if provided.
        email: The caller's email address, if provided.
        notes: Any relevant context about the lead's interest.
    """
    record_lead(name=name, phone=phone, email=email, notes=notes)
    return f"Saved contact details for {name}. Someone will follow up soon."


@function_tool
async def end_call(
    context: RunContext,
    reason: str = "The conversation is complete.",
) -> str:
    """End the current call politely.

    Use this only when the caller clearly wants to hang up or the conversation
    has naturally concluded. The actual disconnect is handled by the telephony
    layer; this signals intent and lets the agent say a closing line.

    Args:
        reason: A short reason the call is ending (for logging).
    """
    logger.info("end_call requested: %s", reason)
    return "Understood, ending the call now. Take care and goodbye!"


@function_tool
async def list_departments(context: RunContext) -> str:
    """List the hospital's departments so the caller can choose one.

    Call this at the start of a booking, before asking anything else, so you
    only ever offer real departments.
    """
    names = list_departments_impl()
    return "Our departments are: " + ", ".join(names) + "."


@function_tool
async def list_doctors(context: RunContext, department: str) -> str:
    """List the doctors in a department so the caller can pick one.

    Call this right after the caller chooses a department, and only offer doctors
    from this list.

    Args:
        department: The department the caller chose (e.g. "Cardiology").
    """
    doctors = list_doctors_impl(department)
    if not doctors:
        available = ", ".join(list_departments_impl())
        return (
            f"I don't have a {department} department. "
            f"We have: {available}. Which would you like?"
        )
    return f"In {department} we have: " + ", ".join(doctors) + "."


@function_tool
async def find_slots(
    context: RunContext,
    start_time: str,
    duration_minutes: int | None = None,
) -> str:
    """Offer nearby available appointment slots around a requested time.

    Use this when the caller's preferred time is taken, to suggest alternatives.

    Args:
        start_time: The caller's preferred start time as an ISO-8601 string.
        duration_minutes: Length in minutes; omit to use the default.
    """
    try:
        slots = suggest_slots(start_time, duration_minutes)
    except AppointmentError as exc:
        return f"{exc} Could you give me a specific date and time?"
    if not slots:
        return "I couldn't find any nearby openings that day. Would another day work?"
    spoken = "; ".join(_spoken_time(s) for s in slots)
    return f"The nearest available times are: {spoken}. Which would you like?"


@function_tool
async def book_appointment(
    context: RunContext,
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
) -> str:
    """Book a hospital appointment once you have collected all the details.

    Only call this after you have asked for, one at a time: department, doctor,
    patient name, phone number, age, gender, symptoms, preferred date, and
    preferred time. If the slot is taken, this returns three nearby alternatives
    to offer — keep offering until the caller picks one, then call this again.

    Args:
        name: The patient's full name.
        department: The chosen department (must be a real one).
        doctor: The chosen doctor within that department.
        preferred_date: The date as YYYY-MM-DD (e.g. "2026-07-08").
        preferred_time: The time as 24-hour HH:MM (e.g. "15:00").
        phone: The patient's phone number.
        age: The patient's age in years.
        gender: The patient's gender.
        symptoms: The patient's symptoms or reason for the visit.
        duration_minutes: Length in minutes; omit to use the default.
    """
    try:
        booking = create_booking(
            name=name,
            department=department,
            doctor=doctor,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            phone=phone,
            age=age,
            gender=gender,
            symptoms=symptoms,
            duration_minutes=duration_minutes,
        )
    except SlotUnavailableError as exc:
        if not exc.alternatives:
            return (
                "That time isn't available and I couldn't find a nearby opening. "
                "Would another day work?"
            )
        spoken = "; ".join(_spoken_time(s) for s in exc.alternatives)
        return (
            f"That time isn't available. The nearest openings are: {spoken}. "
            "Which one works for you?"
        )
    except AppointmentError as exc:
        return f"I couldn't book that. {exc}"

    spoken = _spoken_time(booking.start)
    return (
        f"All set — I've booked {booking.name} with {booking.doctor} in "
        f"{booking.department} for {spoken}. Anything else I can help with?"
    )


@function_tool
async def check_availability(
    context: RunContext,
    start_time: str,
    duration_minutes: int | None = None,
) -> str:
    """Check whether a time slot is free before offering it to the caller.

    Args:
        start_time: The proposed start time as an ISO-8601 string.
        duration_minutes: Length in minutes; omit to use the default.
    """
    try:
        available = is_slot_available(start_time, duration_minutes)
    except AppointmentError as exc:
        return f"{exc} Could you give me a specific date and time?"
    return "That time is available." if available else "That time is already taken."


def get_tools() -> list:
    """Return the list of function tools available to the agent."""
    return [
        get_current_time,
        save_lead,
        list_departments,
        list_doctors,
        check_availability,
        find_slots,
        book_appointment,
        end_call,
    ]
