"""Per-business tenant context for the voice agent.

The dashboard stores each business's profile and AI settings in the shared
database; the agent resolves its tenant from the ``BUSINESS_SLUG`` environment
variable (one worker = one business for the MVP) and loads a fresh
:class:`BusinessContext` at each session start, so dashboard edits apply to the
next call without a restart.

Multi-worker hook: when agents are dispatched per-room later, resolve the slug
from LiveKit room metadata instead of the environment — this module is the
single call site to change.
"""

from __future__ import annotations

import os
from datetime import date

from pydantic import BaseModel

from titan.utils import get_logger

logger = get_logger("titan.tenant")


class BusinessContext(BaseModel):
    """A business row joined with its AI agent settings."""

    business_id: int
    business_name: str
    slug: str
    timezone: str
    business_hours: tuple[int, int]
    status: str
    assistant_name: str
    greeting: str | None = None
    prompt_override: str | None = None
    voice: str
    language: str
    call_flow_config: str = "{}"


def get_business_context(slug: str | None = None) -> BusinessContext | None:
    """Load the business + AI settings for ``slug`` (default: env BUSINESS_SLUG).

    Returns ``None`` when no slug is configured or no matching business exists,
    in which case the agent falls back to its static env-based settings —
    a fresh checkout stays bootable without a seeded database.
    """
    slug = slug or os.environ.get("BUSINESS_SLUG")
    if not slug:
        return None
    try:
        from titan.db import connection as db_connection

        conn = db_connection.get_db()
        row = conn.execute(
            """
            SELECT b.id, b.name, b.slug, b.timezone, b.business_hours_start,
                   b.business_hours_end, b.status,
                   s.assistant_name, s.greeting, s.prompt_override, s.voice,
                   s.language, s.call_flow_config
            FROM businesses b
            LEFT JOIN ai_agent_settings s ON s.business_id = b.id
            WHERE b.slug = ?
            """,
            (slug,),
        ).fetchone()
    except Exception:  # pragma: no cover - defensive: DB must never kill a call
        logger.exception("Failed to load business context for slug %r", slug)
        return None
    if row is None:
        logger.warning("No business found for slug %r; using static settings", slug)
        return None
    return BusinessContext(
        business_id=row["id"],
        business_name=row["name"],
        slug=row["slug"],
        timezone=row["timezone"],
        business_hours=(row["business_hours_start"], row["business_hours_end"]),
        status=row["status"],
        assistant_name=row["assistant_name"] or "TITAN",
        greeting=row["greeting"],
        prompt_override=row["prompt_override"],
        voice=row["voice"] or "pooja",
        language=row["language"] or "hi-IN",
        call_flow_config=row["call_flow_config"] or "{}",
    )


def load_holidays(business_id: int) -> set[date]:
    """Return the business's holiday dates (empty on any failure).

    Loaded fresh per booking attempt so dashboard edits apply immediately;
    the holiday table is tiny, so no caching is warranted.
    """
    try:
        from titan.db import connection as db_connection

        conn = db_connection.get_db()
        rows = conn.execute(
            "SELECT date FROM holiday_schedule WHERE business_id = ?",
            (business_id,),
        ).fetchall()
        return {date.fromisoformat(r["date"]) for r in rows}
    except Exception:  # pragma: no cover - defensive: DB must never kill a call
        logger.exception("Failed to load holidays for business %s", business_id)
        return set()
