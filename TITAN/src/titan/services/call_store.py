"""Call-record store: in-memory default plus a SQLite-backed implementation.

:class:`CallStore` is the original, deliberately simple in-memory store.
:class:`SqliteCallStore` persists the same records to the shared dashboard
database (``calls`` table) so summaries survive restarts and are visible to the
dashboard API. Both expose the same surface; callers depend only on
``get_call_store()`` and the data models defined here.
"""

from __future__ import annotations

import json
from datetime import datetime
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field

Sentiment = Literal["positive", "neutral", "negative"]


class Lead(BaseModel):
    """A caller's contact details captured during a conversation."""

    name: str
    phone: str | None = None
    email: str | None = None
    notes: str | None = None


class CallSummary(BaseModel):
    """Structured summary of a completed conversation.

    Produced by the LLM as a structured output so downstream systems (CRM,
    analytics, dashboards) get consistent, machine-readable call outcomes.
    """

    intent: str = Field(
        description="The caller's primary intent or reason for the call."
    )
    summary: str = Field(
        description="A concise natural-language summary of the conversation."
    )
    key_points: list[str] = Field(
        default_factory=list, description="The most important points discussed."
    )
    action_items: list[str] = Field(
        default_factory=list, description="Concrete follow-up actions, if any."
    )
    sentiment: Sentiment = Field(
        default="neutral", description="Overall caller sentiment."
    )
    follow_up_required: bool = Field(
        default=False, description="Whether a human follow-up is needed."
    )


class CallRecord(BaseModel):
    """Everything the system retains about a single call."""

    call_id: str
    room: str | None = None
    summary: CallSummary | None = None
    leads: list[Lead] = Field(default_factory=list)


class CallStore:
    """Thread-safe, in-memory store of call records and leads."""

    def __init__(self) -> None:
        self._records: dict[str, CallRecord] = {}
        self._pending_leads: list[Lead] = []
        self._lock = Lock()

    def add_lead(self, lead: Lead) -> None:
        """Add a lead captured during the active call."""
        with self._lock:
            self._pending_leads.append(lead)

    def save_summary(
        self, call_id: str, summary: CallSummary, room: str | None = None
    ) -> CallRecord:
        """Persist a call summary, attaching any leads captured during the call."""
        with self._lock:
            record = CallRecord(
                call_id=call_id,
                room=room,
                summary=summary,
                leads=list(self._pending_leads),
            )
            self._records[call_id] = record
            self._pending_leads.clear()
            return record

    def get(self, call_id: str) -> CallRecord | None:
        """Return the record for ``call_id`` if present."""
        with self._lock:
            return self._records.get(call_id)

    def all(self) -> list[CallRecord]:
        """Return all stored call records."""
        with self._lock:
            return list(self._records.values())


_store: CallStore | SqliteCallStore | None = None


class SqliteCallStore:
    """SQLite-backed call store persisting to the shared ``calls`` table.

    Same public surface as :class:`CallStore` (``add_lead`` / ``save_summary``
    / ``get`` / ``all``), plus dashboard extensions: :meth:`list` (tenant-scoped,
    paginated) and :meth:`record_call_lifecycle` (status/duration, including
    missed calls that never produce a summary). Pending leads stay in-memory
    per process — identical semantics to the memory store — and are attached
    to the record when the summary is saved.
    """

    def __init__(self) -> None:
        from titan.db import connection as db_connection

        self._connection = db_connection
        self._pending_leads: list[Lead] = []
        self._lock = Lock()
        db_connection.get_db()  # ensure migrations ran

    def _connect(self):
        return self._connection.connect()

    def add_lead(self, lead: Lead) -> None:
        """Add a lead captured during the active call."""
        with self._lock:
            self._pending_leads.append(lead)

    def save_summary(
        self,
        call_id: str,
        summary: CallSummary,
        room: str | None = None,
        *,
        business_id: int | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> CallRecord:
        """Persist a call summary, attaching any leads captured during the call."""
        with self._lock:
            leads = list(self._pending_leads)
            self._pending_leads.clear()
        record = CallRecord(call_id=call_id, room=room, summary=summary, leads=leads)
        now = datetime.now().astimezone().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO calls (
                    call_id, business_id, room, status, started_at, ended_at,
                    duration_seconds, intent, summary, sentiment, key_points,
                    action_items, follow_up_required, leads, created_at
                ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (call_id) DO UPDATE SET
                    intent = excluded.intent,
                    summary = excluded.summary,
                    sentiment = excluded.sentiment,
                    key_points = excluded.key_points,
                    action_items = excluded.action_items,
                    follow_up_required = excluded.follow_up_required,
                    leads = excluded.leads,
                    status = 'completed'
                """,
                (
                    call_id,
                    business_id,
                    room,
                    started_at,
                    ended_at,
                    _duration_seconds(started_at, ended_at),
                    summary.intent,
                    summary.summary,
                    summary.sentiment,
                    json.dumps(summary.key_points),
                    json.dumps(summary.action_items),
                    int(summary.follow_up_required),
                    json.dumps([lead.model_dump() for lead in leads]),
                    now,
                ),
            )
            conn.commit()
        return record

    def record_call_lifecycle(
        self,
        call_id: str,
        *,
        business_id: int | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        status: str = "completed",
    ) -> None:
        """Upsert call timing/status — e.g. a missed call with no summary."""
        now = datetime.now().astimezone().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO calls (
                    call_id, business_id, status, started_at, ended_at,
                    duration_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (call_id) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    duration_seconds = excluded.duration_seconds
                """,
                (
                    call_id,
                    business_id,
                    status,
                    started_at,
                    ended_at,
                    _duration_seconds(started_at, ended_at),
                    now,
                ),
            )
            conn.commit()

    def get(self, call_id: str) -> CallRecord | None:
        """Return the record for ``call_id`` if present."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def all(self) -> list[CallRecord]:
        """Return all stored call records."""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM calls ORDER BY id").fetchall()
        return [_row_to_record(r) for r in rows]

    def list(
        self,
        business_id: int,
        *,
        from_dt: str | None = None,
        to_dt: str | None = None,
        status: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Paginated raw call rows for one business (dashboard API surface)."""
        where = ["business_id = ?"]
        params: list = [business_id]
        if from_dt:
            where.append("created_at >= ?")
            params.append(from_dt)
        if to_dt:
            where.append("created_at <= ?")
            params.append(to_dt)
        if status:
            where.append("status = ?")
            params.append(status)
        if q:
            where.append("(summary LIKE ? OR intent LIKE ? OR caller_phone LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like])
        clause = " AND ".join(where)
        with self._lock, self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM calls WHERE {clause}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM calls WHERE {clause}"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def _duration_seconds(started_at: str | None, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        delta = datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return max(0, int(delta.total_seconds()))


def _row_to_record(row) -> CallRecord:
    summary = None
    if row["summary"]:
        summary = CallSummary(
            intent=row["intent"] or "",
            summary=row["summary"],
            key_points=json.loads(row["key_points"] or "[]"),
            action_items=json.loads(row["action_items"] or "[]"),
            sentiment=row["sentiment"] or "neutral",
            follow_up_required=bool(row["follow_up_required"]),
        )
    leads = [Lead.model_validate(lead) for lead in json.loads(row["leads"] or "[]")]
    return CallRecord(
        call_id=row["call_id"], room=row["room"], summary=summary, leads=leads
    )


def get_call_store() -> CallStore | SqliteCallStore:
    """Return the process-wide call store singleton.

    Prefers the SQLite-backed store (summaries survive restarts and feed the
    dashboard); falls back to the in-memory store if the database is
    unavailable so a broken data dir never takes the voice agent down.
    """
    global _store
    if _store is None:
        try:
            _store = SqliteCallStore()
        except Exception:  # pragma: no cover - defensive fallback
            _store = CallStore()
    return _store
