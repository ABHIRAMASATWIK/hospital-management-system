"""Google Calendar backend (service-account auth).

This is the real implementation behind the :class:`~titan.services.calendar.CalendarBackend`
seam. It is imported lazily by :func:`titan.services.calendar.get_calendar` only
when ``CALENDAR_BACKEND=google``, so its dependencies stay optional.

Setup:
    1. ``uv sync --extra calendar``
    2. Create a Google Cloud service account, download its JSON key.
    3. Share the target calendar with the service-account email (Make changes to events).
    4. Set ``GOOGLE_SERVICE_ACCOUNT_FILE`` and ``GOOGLE_CALENDAR_ID`` in ``.env.local``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from titan.config import Settings, get_settings
from titan.services.calendar import Appointment, AppointmentError
from titan.utils import get_logger

logger = get_logger("titan.calendar.google")

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarBackend:
    """Books appointments as real Google Calendar events via a service account."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tz = ZoneInfo(self._settings.business_timezone)

        if not self._settings.google_calendar_id:
            raise AppointmentError(
                "GOOGLE_CALENDAR_ID is not set; required for the Google calendar backend."
            )
        if not self._settings.google_service_account_file:
            raise AppointmentError(
                "GOOGLE_SERVICE_ACCOUNT_FILE is not set; required for the Google "
                "calendar backend."
            )

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise AppointmentError(
                "Google Calendar support is not installed. Run "
                "`uv sync --extra calendar` to enable it."
            ) from exc

        credentials = service_account.Credentials.from_service_account_file(
            self._settings.google_service_account_file, scopes=_SCOPES
        )
        # cache_discovery=False avoids a noisy warning and a file-cache dependency.
        self._service = build(
            "calendar", "v3", credentials=credentials, cache_discovery=False
        )
        self._calendar_id = self._settings.google_calendar_id

    def _aware(self, when: datetime) -> datetime:
        return when if when.tzinfo is not None else when.replace(tzinfo=self._tz)

    def create_appointment(self, appt: Appointment) -> Appointment:
        start = self._aware(appt.start)
        end = start + timedelta(minutes=appt.duration_minutes)

        description_parts = [p for p in (appt.notes,) if p]
        if appt.phone:
            description_parts.append(f"Phone: {appt.phone}")
        if appt.email:
            description_parts.append(f"Email: {appt.email}")

        body = {
            "summary": f"Call with {appt.name}",
            "description": "\n".join(description_parts) or None,
            "start": {"dateTime": start.isoformat(), "timeZone": str(self._tz)},
            "end": {"dateTime": end.isoformat(), "timeZone": str(self._tz)},
        }
        if appt.email:
            body["attendees"] = [{"email": appt.email}]

        try:
            event = (
                self._service.events()
                .insert(calendarId=self._calendar_id, body=body)
                .execute()
            )
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Google Calendar insert failed: %s", exc)
            raise AppointmentError("Could not create the calendar event.") from exc

        return appt.model_copy(
            update={
                "start": start,
                "event_id": event.get("id"),
                "html_link": event.get("htmlLink"),
            }
        )

    def list_appointments(self, day: date) -> list[Appointment]:
        day_start = datetime.combine(day, time.min, tzinfo=self._tz)
        day_end = day_start + timedelta(days=1)
        try:
            resp = (
                self._service.events()
                .list(
                    calendarId=self._calendar_id,
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as exc:  # pragma: no cover - network path
            logger.error("Google Calendar list failed: %s", exc)
            raise AppointmentError("Could not read the calendar.") from exc

        appointments: list[Appointment] = []
        for event in resp.get("items", []):
            start_raw = event.get("start", {}).get("dateTime")
            end_raw = event.get("end", {}).get("dateTime")
            if not start_raw or not end_raw:
                continue  # skip all-day events
            start = datetime.fromisoformat(start_raw)
            end = datetime.fromisoformat(end_raw)
            appointments.append(
                Appointment(
                    name=event.get("summary", "Busy"),
                    start=start,
                    duration_minutes=max(1, int((end - start).total_seconds() // 60)),
                    event_id=event.get("id"),
                    html_link=event.get("htmlLink"),
                )
            )
        return appointments
