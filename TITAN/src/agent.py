"""LiveKit entrypoint for the TITAN AI Call Agent.

This file is intentionally thin: it is the process entrypoint the Dockerfile and
LiveKit CLI invoke (``uv run src/agent.py {console,dev,start}``). All business
logic lives in the ``titan`` package. Responsibilities here:

1. Load environment configuration.
2. Configure logging.
3. Register the LiveKit RTC session and wire the voice pipeline.
4. On shutdown, generate and store a structured call summary.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, cli

# Load `.env.local` (LiveKit convention) from the project root before settings
# are read, so environment variables are available to the whole process.
load_dotenv(Path(__file__).resolve().parents[1] / ".env.local")

from titan.config import get_settings  # noqa: E402 - must follow dotenv load
from titan.services import (  # noqa: E402
    Assistant,
    build_session,
    generate_call_summary,
    get_call_store,
)
from titan.services.session import build_room_options  # noqa: E402
from titan.services.summary import format_transcript  # noqa: E402
from titan.services.tenant import get_business_context  # noqa: E402
from titan.utils import get_logger, setup_logging  # noqa: E402

settings = get_settings()
setup_logging(settings.log_level)
logger = get_logger("agent")

server = AgentServer()


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Handle a single call: start the voice session and summarize on shutdown."""
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info("Starting session for room %s", ctx.room.name)

    # Re-read the tenant context each session so dashboard edits apply to the
    # next call without a restart. None → static env settings (single-tenant).
    context = get_business_context()
    if context is not None:
        logger.info(
            "Using business context %r (id=%s)", context.slug, context.business_id
        )

    session = build_session(settings, context)

    async def _summarize_on_shutdown() -> None:
        """Generate and persist a structured summary when the call ends."""
        transcript = format_transcript(session.history)
        summary = await generate_call_summary(transcript, settings)
        store = get_call_store()
        kwargs = {}
        if context is not None and hasattr(store, "list"):
            kwargs["business_id"] = context.business_id  # SQLite store only
        record = store.save_summary(
            call_id=ctx.room.name, summary=summary, room=ctx.room.name, **kwargs
        )
        logger.info(
            "Call %s summary: intent=%r sentiment=%s follow_up=%s",
            record.call_id,
            summary.intent,
            summary.sentiment,
            summary.follow_up_required,
        )

    ctx.add_shutdown_callback(_summarize_on_shutdown)

    await session.start(
        agent=Assistant(settings, context=context),
        room=ctx.room,
        room_options=build_room_options(),
    )

    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
