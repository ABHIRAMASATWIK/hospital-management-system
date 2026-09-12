"""FastAPI backend for the TITAN AI Employees dashboard.

Serves two surfaces:

- ``/api/v1/*`` — the authenticated dashboard API (auth, super-admin fleet
  management, business-scoped resources). See :mod:`titan.api.routers`.
- Legacy read-only endpoints (``/health``, ``/calls``, ``/leads``) kept for
  backward compatibility with earlier integrations; ``/calls`` now reads the
  persistent store, so summaries survive restarts.

Run with the ``api`` extra installed::

    uv run uvicorn titan.api.app:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from titan import __version__
from titan.config import get_settings
from titan.services.call_store import CallRecord, CallSummary, Lead, get_call_store


def create_app() -> FastAPI:
    """Create and configure the FastAPI dashboard application."""
    settings = get_settings()
    app = FastAPI(
        title="TITAN AI Employees — Dashboard API",
        version=__version__,
        summary="Multi-tenant management API for TITAN AI Employees.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o.strip() for o in settings.cors_origins.split(",") if o.strip()
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from titan.api.routers import admin_router, auth_router, business_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(business_router, prefix="/api/v1")

    # ---- legacy read-only surface (pre-dashboard integrations) ----------
    store = get_call_store()

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness/readiness probe."""
        return {"status": "ok", "agent": settings.agent_name, "version": __version__}

    @app.get("/calls", response_model=list[CallRecord])
    async def list_calls() -> list[CallRecord]:
        """Return all stored call records."""
        return store.all()

    @app.get("/calls/{call_id}/summary", response_model=CallSummary)
    async def get_call_summary(call_id: str) -> CallSummary:
        """Return the structured summary for a single call."""
        record = store.get(call_id)
        if record is None or record.summary is None:
            raise HTTPException(status_code=404, detail="Call summary not found.")
        return record.summary

    @app.get("/leads", response_model=list[Lead])
    async def list_leads() -> list[Lead]:
        """Return all leads captured across stored calls."""
        return [lead for record in store.all() for lead in record.leads]

    return app


# Module-level ASGI app for `uvicorn titan.api.app:app`.
app = create_app()
