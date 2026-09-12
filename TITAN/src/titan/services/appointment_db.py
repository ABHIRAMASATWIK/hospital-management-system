"""SQLite persistence for confirmed bookings — the primary application datastore.

Google Calendar is the scheduling source of truth; this SQLite database is where
the application's own booking records live (patient details, the calendar event
id, status, and timestamps). Like the other seams (:mod:`call_store`,
:mod:`calendar`), callers depend only on :func:`get_appointment_db` and can swap
in a different database without touching the booking flow.

The schema is managed by :mod:`titan.db.migrations` (shared with the dashboard
API), so the voice agent and the dashboard read and write the same tables. The
voice agent's original ``save(booking)`` / ``all()`` surface is unchanged; the
dashboard additionally uses the tenant-scoped CRUD methods
(:meth:`AppointmentDB.list`, :meth:`~AppointmentDB.get`,
:meth:`~AppointmentDB.update`, :meth:`~AppointmentDB.cancel`,
:meth:`~AppointmentDB.reschedule`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from threading import Lock
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from titan.config import Settings, get_settings
from titan.db import connection as db_connection
from titan.utils import get_logger

if TYPE_CHECKING:  # avoid a runtime import cycle (booking imports this module)
    from titan.services.booking import Booking

logger = get_logger("titan.appointment_db")

_INSERT = """
INSERT INTO appointments (
    name, phone, age, gender, department, doctor, symptoms,
    appointment_start, appointment_end, event_id, status, created_at, business_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Columns the dashboard may edit via `update()`. Anything else is rejected.
_UPDATABLE_FIELDS = frozenset(
    {
        "name",
        "phone",
        "age",
        "gender",
        "department",
        "doctor",
        "symptoms",
        "appointment_start",
        "appointment_end",
        "event_id",
        "status",
        "business_id",
        "payment_status",
        "payment_method",
        "payment_notes",
        "completed_at",
    }
)


def _now_iso() -> str:
    return datetime.now(ZoneInfo(get_settings().business_timezone)).isoformat()


class AppointmentDB:
    """Thread-safe SQLite store of confirmed bookings.

    A fresh connection is opened per operation (simplest correct approach across
    threads); the schema is created/upgraded once via the shared migration
    runner, so no manual setup is required.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        # An explicitly-passed Settings pins this store to its database file
        # (test isolation); otherwise the shared dashboard database is used.
        self._path = settings.database_path if settings is not None else None
        self._lock = Lock()
        self._init_db()

    @classmethod
    def for_dashboard(cls) -> AppointmentDB:
        """Explicit constructor used by the API layer (same behavior)."""
        return cls()

    def _connect(self) -> sqlite3.Connection:
        return db_connection.connect(self._path)

    def _init_db(self) -> None:
        from titan.db.migrations import run_migrations

        if self._path is not None:
            with self._connect() as conn:
                run_migrations(conn)
        else:
            # get_db() applies migrations (creating/upgrading `appointments`).
            db_connection.get_db()
        logger.info("SQLite appointments store ready")

    # -- voice-agent surface (unchanged) -----------------------------------

    def save(self, booking: Booking, business_id: int | None = None) -> int:
        """Persist ``booking`` and return its new row id."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                _INSERT,
                (
                    booking.name,
                    booking.phone,
                    booking.age,
                    booking.gender,
                    booking.department,
                    booking.doctor,
                    booking.symptoms,
                    booking.start.isoformat(),
                    booking.end.isoformat(),
                    booking.event_id,
                    booking.status,
                    booking.created_at.isoformat() if booking.created_at else "",
                    business_id,
                ),
            )
            conn.commit()
            row_id = int(cur.lastrowid)
        logger.info("Saved booking #%d for %s to SQLite", row_id, booking.name)
        return row_id

    def all(self) -> list[dict]:
        """Return every stored booking as a list of dicts (ordered by id)."""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM appointments ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    # -- dashboard CRUD (tenant-scoped) -------------------------------------

    def get(self, row_id: int, business_id: int | None) -> dict | None:
        """Return one appointment; ``business_id`` (when set) scopes the lookup."""
        sql = "SELECT * FROM appointments WHERE id = ?"
        params: list = [row_id]
        if business_id is not None:
            sql += " AND business_id = ?"
            params.append(business_id)
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def list(
        self,
        business_id: int,
        *,
        from_dt: str | None = None,
        to_dt: str | None = None,
        status: str | None = None,
        doctor: str | None = None,
        department: str | None = None,
        payment_status: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Paginated, filtered appointments for one business.

        Returns ``{"items": [...], "total": int, "page": int, "page_size": int}``.
        """
        where = ["business_id = ?"]
        params: list = [business_id]
        if from_dt:
            where.append("appointment_start >= ?")
            params.append(from_dt)
        if to_dt:
            where.append("appointment_start <= ?")
            params.append(to_dt)
        if status:
            where.append("status = ?")
            params.append(status)
        if doctor:
            where.append("doctor = ?")
            params.append(doctor)
        if department:
            where.append("department = ?")
            params.append(department)
        if payment_status:
            where.append("payment_status = ?")
            params.append(payment_status)
        if q:
            where.append("(name LIKE ? OR phone LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        clause = " AND ".join(where)

        with self._lock, self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM appointments WHERE {clause}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM appointments WHERE {clause}"
                " ORDER BY appointment_start LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def update(self, row_id: int, business_id: int, fields: dict) -> bool:
        """Edit whitelisted fields; returns True if a row was changed."""
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update fields: {sorted(unknown)}")
        if not fields:
            return False
        assignments = ", ".join(f"{k} = ?" for k in fields)
        params = [*fields.values(), _now_iso(), row_id, business_id]
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE appointments SET {assignments}, updated_at = ?"
                " WHERE id = ? AND business_id = ?",
                params,
            )
            conn.commit()
        return cur.rowcount > 0

    def cancel(self, row_id: int, business_id: int) -> bool:
        """Mark an appointment cancelled; returns True if a row was changed."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE appointments SET status = 'cancelled', cancelled_at = ?,"
                " updated_at = ? WHERE id = ? AND business_id = ?"
                " AND status != 'cancelled'",
                (now, now, row_id, business_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def reschedule(
        self, row_id: int, business_id: int, *, new_start: str, new_end: str | None
    ) -> bool:
        """Move an appointment to a new time; returns True if a row was changed."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE appointments SET appointment_start = ?, appointment_end = ?,"
                " status = 'rescheduled', updated_at = ?"
                " WHERE id = ? AND business_id = ?",
                (new_start, new_end, _now_iso(), row_id, business_id),
            )
            conn.commit()
        return cur.rowcount > 0


_db: AppointmentDB | None = None


def get_appointment_db() -> AppointmentDB:
    """Return the process-wide :class:`AppointmentDB` singleton."""
    global _db
    if _db is None:
        _db = AppointmentDB()
    return _db
