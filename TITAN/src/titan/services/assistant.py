"""The TITAN assistant agent definition.

The :class:`Assistant` holds the persona (instructions) and the set of callable
tools. The voice pipeline (LLM/STT/TTS) is configured separately on the
``AgentSession`` (see :mod:`titan.services.session`), keeping model wiring and
agent behavior cleanly decoupled.

The LLM node is wrapped so that a provider failure mid-call (e.g. a Gemini 429
quota error) degrades into a short spoken apology instead of dropping the turn
with a stack trace.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator

from livekit.agents import Agent, APIError
from livekit.agents.llm import ChatChunk, ChatContext, Tool
from livekit.agents.voice.agent import ModelSettings

from titan.config import Settings, get_settings
from titan.prompts import build_instructions
from titan.services.tenant import BusinessContext
from titan.tools import get_tools
from titan.utils import get_logger

logger = get_logger("titan.assistant")


def _is_rate_limited(exc: BaseException) -> bool:
    """True if ``exc`` (or any error it wraps) is a 429 / quota-exhaustion error.

    LiveKit wraps the provider error: after exhausting retries it raises an
    ``APIConnectionError`` ("failed to generate LLM completion after N attempts")
    whose ``__cause__`` is the real ``APIStatusError(status_code=429, ...)``. So we
    walk the ``__cause__`` / ``__context__`` chain rather than inspecting only the
    top-level exception.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "status_code", None) == 429:
            return True
        haystack = f"{current} {getattr(current, 'body', '') or ''}".lower()
        if "resource_exhausted" in haystack or "quota" in haystack:
            return True
        current = current.__cause__ or current.__context__
    return False


def fallback_message(exc: Exception) -> str:
    """Return a warm, spoken-friendly line to say when the LLM call fails.

    Rate-limit / quota exhaustion gets a "busy, try again shortly" message; any
    other API failure gets a generic "brief technical issue" apology.
    """
    if _is_rate_limited(exc):
        return (
            "Sorry, I'm a little busy at the moment. "
            "Could you please say that again in a few seconds?"
        )
    return "Sorry, I ran into a brief technical issue. Could you please repeat that?"


async def guard_llm_stream(
    stream: AsyncIterable[ChatChunk | str],
) -> AsyncIterator[ChatChunk | str]:
    """Pass an LLM node stream through, converting an API failure into a spoken line.

    Chunks flow through untouched. If the underlying stream raises an
    :class:`~livekit.agents.APIError` (covers status errors like 429 and
    connection errors), the failure is logged and a single fallback string is
    yielded so the caller always hears a graceful response.
    """
    try:
        async for chunk in stream:
            yield chunk
    except APIError as exc:
        logger.warning("LLM node failed; speaking a fallback line: %s", exc)
        yield fallback_message(exc)


class Assistant(Agent):
    """The TITAN voice assistant persona with its tools."""

    def __init__(
        self,
        settings: Settings | None = None,
        context: BusinessContext | None = None,
    ) -> None:
        settings = settings or get_settings()
        if context is not None:
            # Dashboard-configured tenant overrides; static env settings remain
            # the fallback so a fresh checkout works without a seeded database.
            instructions = build_instructions(
                assistant_name=context.assistant_name,
                user_name=settings.user_name,
                persona=settings.persona,
                hospital_name=context.business_name,
                greeting=context.greeting,
                prompt_override=context.prompt_override,
            )
        else:
            instructions = build_instructions(
                assistant_name=settings.assistant_name,
                user_name=settings.user_name,
                persona=settings.persona,
                hospital_name=settings.hospital_name,
            )
        super().__init__(instructions=instructions, tools=get_tools())

    async def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list[Tool],
        model_settings: ModelSettings,
    ) -> AsyncIterator[ChatChunk | str]:
        """Wrap the default LLM node so provider failures degrade gracefully."""
        stream = Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        async for out in guard_llm_stream(stream):
            yield out
