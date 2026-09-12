"""Business-scoped dashboard endpoints.

Every route resolves its tenant through :func:`titan.api.auth.get_tenant_id`,
so a business user is always locked to their own business and a super_admin
addresses a tenant explicitly with ``?business_id=``.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from titan.api.auth import get_tenant_id, require_permission
from titan.db import connection as db_connection
from titan.services.appointment_db import AppointmentDB
from titan.services.call_store import SqliteCallStore
from titan.utils import get_logger

logger = get_logger("titan.api.business")

router = APIRouter(tags=["business"])


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _business_row(business_id: int) -> sqlite3.Row:
    row = (
        db_connection.get_db()
        .execute("SELECT * FROM businesses WHERE id = ?", (business_id,))
        .fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Business not found.")
    return row


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------


@router.get(
    "/overview", dependencies=[Depends(require_permission("business:overview"))]
)
def overview(tenant_id: int = Depends(get_tenant_id)) -> dict:
    business = _business_row(tenant_id)
    tz = ZoneInfo(business["timezone"])
    now = datetime.now(tz)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    day_end = now.replace(hour=23, minute=59, second=59).isoformat()
    conn = db_connection.get_db()

    def one(sql: str, params: tuple) -> int:
        return conn.execute(sql, params).fetchone()[0]

    today_calls = one(
        "SELECT COUNT(*) FROM calls WHERE business_id = ? AND created_at BETWEEN ? AND ?",
        (tenant_id, day_start, day_end),
    )
    missed_calls = one(
        "SELECT COUNT(*) FROM calls WHERE business_id = ? AND status = 'missed'"
        " AND created_at BETWEEN ? AND ?",
        (tenant_id, day_start, day_end),
    )
    today_appointments = one(
        "SELECT COUNT(*) FROM appointments WHERE business_id = ?"
        " AND appointment_start BETWEEN ? AND ? AND status != 'cancelled'",
        (tenant_id, day_start, day_end),
    )
    upcoming = conn.execute(
        "SELECT * FROM appointments WHERE business_id = ? AND appointment_start > ?"
        " AND status NOT IN ('cancelled', 'completed')"
        " ORDER BY appointment_start LIMIT 5",
        (tenant_id, now.isoformat()),
    ).fetchall()
    has_settings = (
        conn.execute(
            "SELECT 1 FROM ai_agent_settings WHERE business_id = ?", (tenant_id,)
        ).fetchone()
        is not None
    )
    return {
        "today_calls": today_calls,
        "today_appointments": today_appointments,
        "missed_calls": missed_calls,
        "upcoming_appointments": [dict(r) for r in upcoming],
        "ai_status": (
            "active" if has_settings and business["status"] == "active" else "inactive"
        ),
    }


# --------------------------------------------------------------------------
# AI settings
# --------------------------------------------------------------------------


class AISettingsUpdate(BaseModel):
    assistant_name: str | None = None
    greeting: str | None = None
    prompt_override: str | None = None
    voice: str | None = None
    language: str | None = None
    call_flow_config: str | None = None


@router.get(
    "/ai-settings",
    dependencies=[Depends(require_permission("business:ai-settings:read"))],
)
def get_ai_settings(tenant_id: int = Depends(get_tenant_id)) -> dict:
    row = (
        db_connection.get_db()
        .execute("SELECT * FROM ai_agent_settings WHERE business_id = ?", (tenant_id,))
        .fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="AI settings not found.")
    return dict(row)


@router.put(
    "/ai-settings",
    dependencies=[Depends(require_permission("business:ai-settings:write"))],
)
def update_ai_settings(
    body: AISettingsUpdate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    fields = body.model_dump(exclude_none=True)
    conn = db_connection.get_db()
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = conn.execute(
            f"UPDATE ai_agent_settings SET {assignments}, updated_at = ?"
            " WHERE business_id = ?",
            [*fields.values(), _now_iso(), tenant_id],
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="AI settings not found.")
    return get_ai_settings(tenant_id)


# --------------------------------------------------------------------------
# Appointments
# --------------------------------------------------------------------------


class AppointmentCreate(BaseModel):
    name: str
    phone: str | None = None
    age: int | None = None
    gender: str | None = None
    department: str
    doctor: str
    symptoms: str | None = None
    appointment_start: str
    duration_minutes: int = Field(default=30, gt=0)


class AppointmentUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    age: int | None = None
    gender: str | None = None
    department: str | None = None
    doctor: str | None = None
    symptoms: str | None = None
    status: str | None = None
    appointment_start: str | None = None
    duration_minutes: int = Field(default=30, gt=0)
    payment_status: Literal["pending", "paid", "refunded"] | None = None
    payment_method: Literal["UPI", "Cash", "Card", "Other"] | None = None
    payment_notes: str | None = None


class RescheduleRequest(BaseModel):
    new_start: str
    duration_minutes: int = Field(default=30, gt=0)


def _appointment_db() -> AppointmentDB:
    return AppointmentDB()


def _validate_bookable(business: sqlite3.Row, start: datetime) -> None:
    """Reject times outside business hours or on a holiday (422)."""
    if (
        not business["business_hours_start"]
        <= start.hour
        < business["business_hours_end"]
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Time is outside business hours"
                f" ({business['business_hours_start']}:00-"
                f"{business['business_hours_end']}:00)."
            ),
        )
    holiday = (
        db_connection.get_db()
        .execute(
            "SELECT label FROM holiday_schedule WHERE business_id = ? AND date = ?",
            (business["id"], start.date().isoformat()),
        )
        .fetchone()
    )
    if holiday:
        raise HTTPException(
            status_code=422,
            detail=f"That date is a holiday ({holiday['label'] or 'closed'}).",
        )


def _parse_start(value: str, business: sqlite3.Row) -> datetime:
    try:
        start = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid datetime.") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo(business["timezone"]))
    return start


@router.get(
    "/appointments",
    dependencies=[Depends(require_permission("business:appointments:read"))],
)
def list_appointments(
    tenant_id: int = Depends(get_tenant_id),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    status: str | None = None,
    doctor: str | None = None,
    department: str | None = None,
    payment_status: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    return _appointment_db().list(
        tenant_id,
        from_dt=from_,
        to_dt=to,
        status=status,
        doctor=doctor,
        department=department,
        payment_status=payment_status,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/appointments",
    status_code=201,
    dependencies=[Depends(require_permission("business:appointments:write"))],
)
def create_appointment(
    body: AppointmentCreate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    from titan.services.booking import Booking

    business = _business_row(tenant_id)
    start = _parse_start(body.appointment_start, business)
    _validate_bookable(business, start)
    booking = Booking(
        name=body.name,
        phone=body.phone,
        age=body.age,
        gender=body.gender,
        department=body.department,
        doctor=body.doctor,
        symptoms=body.symptoms,
        start=start,
        duration_minutes=body.duration_minutes,
        status="confirmed",
        created_at=datetime.now(ZoneInfo(business["timezone"])),
    )
    db = _appointment_db()
    row_id = db.save(booking, business_id=tenant_id)
    return db.get(row_id, business_id=tenant_id)


@router.get(
    "/appointments/{appointment_id}",
    dependencies=[Depends(require_permission("business:appointments:read"))],
)
def get_appointment(
    appointment_id: int, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    row = _appointment_db().get(appointment_id, business_id=tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return row


@router.patch(
    "/appointments/{appointment_id}",
    dependencies=[Depends(require_permission("business:appointments:write"))],
)
def update_appointment(
    appointment_id: int,
    body: AppointmentUpdate,
    tenant_id: int = Depends(get_tenant_id),
) -> dict:
    db = _appointment_db()
    existing = db.get(appointment_id, business_id=tenant_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    fields = body.model_dump(exclude_none=True)
    duration = fields.pop("duration_minutes")
    if "appointment_start" in fields:
        # Unlike /reschedule, an inline edit keeps the current status.
        business = _business_row(tenant_id)
        start = _parse_start(fields["appointment_start"], business)
        _validate_bookable(business, start)
        fields["appointment_start"] = start.isoformat()
        fields["appointment_end"] = (start + timedelta(minutes=duration)).isoformat()
    status = fields.pop("status", None)
    if status == "cancelled":
        # A failed cancel means already-cancelled or invisible; the get()
        # below distinguishes (404 only when the row isn't visible).
        db.cancel(appointment_id, business_id=tenant_id)
    elif status is not None:
        fields["status"] = status
        if status == "completed":
            if existing["status"] != "completed":
                fields["completed_at"] = _now_iso()
        elif existing["completed_at"]:
            fields["completed_at"] = None
    if fields and not db.update(appointment_id, business_id=tenant_id, fields=fields):
        raise HTTPException(status_code=404, detail="Appointment not found.")
    row = db.get(appointment_id, business_id=tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return row


@router.post(
    "/appointments/{appointment_id}/reschedule",
    dependencies=[Depends(require_permission("business:appointments:write"))],
)
def reschedule_appointment(
    appointment_id: int,
    body: RescheduleRequest,
    tenant_id: int = Depends(get_tenant_id),
) -> dict:
    business = _business_row(tenant_id)
    start = _parse_start(body.new_start, business)
    _validate_bookable(business, start)
    db = _appointment_db()
    end = start + timedelta(minutes=body.duration_minutes)
    if not db.reschedule(
        appointment_id,
        business_id=tenant_id,
        new_start=start.isoformat(),
        new_end=end.isoformat(),
    ):
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return db.get(appointment_id, business_id=tenant_id)


# --------------------------------------------------------------------------
# Departments & doctors
# --------------------------------------------------------------------------


class DepartmentCreate(BaseModel):
    name: str


class DoctorCreate(BaseModel):
    department_id: int
    name: str
    specialization: str | None = None
    availability: str = "{}"


class DoctorUpdate(BaseModel):
    department_id: int | None = None
    name: str | None = None
    specialization: str | None = None
    availability: str | None = None
    is_active: bool | None = None


@router.get(
    "/departments", dependencies=[Depends(require_permission("business:staff:read"))]
)
def list_departments(tenant_id: int = Depends(get_tenant_id)) -> list[dict]:
    rows = (
        db_connection.get_db()
        .execute(
            "SELECT * FROM departments WHERE business_id = ? ORDER BY name",
            (tenant_id,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


@router.post(
    "/departments",
    status_code=201,
    dependencies=[Depends(require_permission("business:staff:write"))],
)
def create_department(
    body: DepartmentCreate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    conn = db_connection.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO departments (business_id, name) VALUES (?, ?)",
            (tenant_id, body.name),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="Department already exists."
        ) from exc
    return {"id": int(cur.lastrowid), "business_id": tenant_id, "name": body.name}


@router.delete(
    "/departments/{department_id}",
    status_code=204,
    dependencies=[Depends(require_permission("business:staff:write"))],
)
def delete_department(
    department_id: int, tenant_id: int = Depends(get_tenant_id)
) -> None:
    conn = db_connection.get_db()
    in_use = conn.execute(
        "SELECT 1 FROM doctors WHERE department_id = ? AND business_id = ?"
        " AND is_active = 1 LIMIT 1",
        (department_id, tenant_id),
    ).fetchone()
    if in_use:
        raise HTTPException(
            status_code=409, detail="Department still has active doctors."
        )
    conn.execute(
        "DELETE FROM departments WHERE id = ? AND business_id = ?",
        (department_id, tenant_id),
    )
    conn.commit()


@router.get(
    "/doctors", dependencies=[Depends(require_permission("business:staff:read"))]
)
def list_doctors(
    tenant_id: int = Depends(get_tenant_id),
    include_inactive: bool = False,
) -> dict:
    sql = (
        "SELECT d.*, dep.name AS department_name FROM doctors d"
        " JOIN departments dep ON dep.id = d.department_id"
        " WHERE d.business_id = ?"
    )
    if not include_inactive:
        sql += " AND d.is_active = 1"
    rows = (
        db_connection.get_db()
        .execute(sql + " ORDER BY d.name", (tenant_id,))
        .fetchall()
    )
    return {"items": [dict(r) for r in rows], "total": len(rows)}


@router.post(
    "/doctors",
    status_code=201,
    dependencies=[Depends(require_permission("business:staff:write"))],
)
def create_doctor(body: DoctorCreate, tenant_id: int = Depends(get_tenant_id)) -> dict:
    conn = db_connection.get_db()
    dept = conn.execute(
        "SELECT id FROM departments WHERE id = ? AND business_id = ?",
        (body.department_id, tenant_id),
    ).fetchone()
    if dept is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    cur = conn.execute(
        "INSERT INTO doctors (business_id, department_id, name, specialization,"
        " availability, is_active) VALUES (?, ?, ?, ?, ?, 1)",
        (
            tenant_id,
            body.department_id,
            body.name,
            body.specialization,
            body.availability,
        ),
    )
    conn.commit()
    return {"id": int(cur.lastrowid), **body.model_dump(), "business_id": tenant_id}


@router.patch(
    "/doctors/{doctor_id}",
    dependencies=[Depends(require_permission("business:staff:write"))],
)
def update_doctor(
    doctor_id: int, body: DoctorUpdate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    fields = body.model_dump(exclude_none=True)
    if "is_active" in fields:
        fields["is_active"] = int(fields["is_active"])
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update.")
    conn = db_connection.get_db()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE doctors SET {assignments} WHERE id = ? AND business_id = ?",
        [*fields.values(), doctor_id, tenant_id],
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    row = conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    return dict(row)


@router.delete(
    "/doctors/{doctor_id}",
    status_code=204,
    dependencies=[Depends(require_permission("business:staff:write"))],
)
def delete_doctor(doctor_id: int, tenant_id: int = Depends(get_tenant_id)) -> None:
    conn = db_connection.get_db()
    cur = conn.execute(
        "UPDATE doctors SET is_active = 0 WHERE id = ? AND business_id = ?",
        (doctor_id, tenant_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Doctor not found.")


# --------------------------------------------------------------------------
# Call history
# --------------------------------------------------------------------------


@router.get("/calls", dependencies=[Depends(require_permission("business:calls:read"))])
def list_calls(
    tenant_id: int = Depends(get_tenant_id),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    return SqliteCallStore().list(
        tenant_id,
        from_dt=from_,
        to_dt=to,
        status=status,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/calls/{row_id}",
    dependencies=[Depends(require_permission("business:calls:read"))],
)
def get_call(row_id: int, tenant_id: int = Depends(get_tenant_id)) -> dict:
    row = (
        db_connection.get_db()
        .execute(
            "SELECT * FROM calls WHERE id = ? AND business_id = ?", (row_id, tenant_id)
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    return dict(row)


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------


@router.get(
    "/analytics", dependencies=[Depends(require_permission("business:analytics:read"))]
)
def analytics(
    tenant_id: int = Depends(get_tenant_id),
    from_: str = Query(alias="from"),
    to: str = Query(),
) -> dict:
    conn = db_connection.get_db()
    # ISO timestamps sort lexicographically, so date-prefix comparison works.
    call_rows = conn.execute(
        "SELECT created_at, status FROM calls WHERE business_id = ?"
        " AND substr(created_at, 1, 10) BETWEEN ? AND ?",
        (tenant_id, from_, to),
    ).fetchall()
    appt_rows = conn.execute(
        "SELECT appointment_start FROM appointments WHERE business_id = ?"
        " AND substr(appointment_start, 1, 10) BETWEEN ? AND ?"
        " AND status != 'cancelled'",
        (tenant_id, from_, to),
    ).fetchall()

    calls_per_day: Counter[str] = Counter()
    missed_per_day: Counter[str] = Counter()
    peak_hours = [0] * 24
    for row in call_rows:
        day = row["created_at"][:10]
        calls_per_day[day] += 1
        if row["status"] == "missed":
            missed_per_day[day] += 1
        with contextlib.suppress(ValueError):
            peak_hours[datetime.fromisoformat(row["created_at"]).hour] += 1
    appointments_per_day: Counter[str] = Counter()
    for row in appt_rows:
        appointments_per_day[row["appointment_start"][:10]] += 1

    completed = sum(1 for r in call_rows if r["status"] == "completed")
    # MVP conversion: appointments booked / completed calls in the window.
    conversion = round(len(appt_rows) / completed, 3) if completed else 0.0
    as_series = lambda c: [  # noqa: E731
        {"date": d, "count": n} for d, n in sorted(c.items())
    ]
    return {
        "calls_per_day": as_series(calls_per_day),
        "appointments_per_day": as_series(appointments_per_day),
        "missed_per_day": as_series(missed_per_day),
        "conversion_rate": conversion,
        "peak_hours": peak_hours,
    }


# --------------------------------------------------------------------------
# Business settings & holidays
# --------------------------------------------------------------------------


class BusinessSettingsUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    phone: str | None = None
    timezone: str | None = None
    business_hours_start: int | None = Field(default=None, ge=0, le=23)
    business_hours_end: int | None = Field(default=None, ge=1, le=24)
    working_days: str | None = None


class HolidayCreate(BaseModel):
    date: str
    label: str | None = None


@router.get(
    "/settings", dependencies=[Depends(require_permission("business:settings:read"))]
)
def get_business_settings(tenant_id: int = Depends(get_tenant_id)) -> dict:
    return dict(_business_row(tenant_id))


@router.put(
    "/settings", dependencies=[Depends(require_permission("business:settings:write"))]
)
def update_business_settings(
    body: BusinessSettingsUpdate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    fields = body.model_dump(exclude_none=True)
    if fields:
        conn = db_connection.get_db()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE businesses SET {assignments}, updated_at = ? WHERE id = ?",
            [*fields.values(), _now_iso(), tenant_id],
        )
        conn.commit()
    return dict(_business_row(tenant_id))


@router.get(
    "/holidays", dependencies=[Depends(require_permission("business:settings:read"))]
)
def list_holidays(tenant_id: int = Depends(get_tenant_id)) -> list[dict]:
    rows = (
        db_connection.get_db()
        .execute(
            "SELECT * FROM holiday_schedule WHERE business_id = ? ORDER BY date",
            (tenant_id,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


@router.post(
    "/holidays",
    status_code=201,
    dependencies=[Depends(require_permission("business:settings:write"))],
)
def create_holiday(
    body: HolidayCreate, tenant_id: int = Depends(get_tenant_id)
) -> dict:
    conn = db_connection.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO holiday_schedule (business_id, date, label) VALUES (?, ?, ?)",
            (tenant_id, body.date, body.label),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Holiday already exists.") from exc
    return {"id": int(cur.lastrowid), "business_id": tenant_id, **body.model_dump()}


@router.delete(
    "/holidays/{holiday_id}",
    status_code=204,
    dependencies=[Depends(require_permission("business:settings:write"))],
)
def delete_holiday(holiday_id: int, tenant_id: int = Depends(get_tenant_id)) -> None:
    conn = db_connection.get_db()
    cur = conn.execute(
        "DELETE FROM holiday_schedule WHERE id = ? AND business_id = ?",
        (holiday_id, tenant_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Holiday not found.")
