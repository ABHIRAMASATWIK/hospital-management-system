"""Offline tests for graceful LLM-failure handling in the Assistant.

These verify that an API failure (e.g. a Gemini 429 quota error) mid-generation
is turned into a single spoken fallback line instead of propagating and dropping
the turn — without needing a live model or session.
"""

import pytest
from livekit.agents import APIConnectionError, APIStatusError

from titan.services.assistant import fallback_message, guard_llm_stream


def _wrapped_quota_error() -> APIConnectionError:
    """Mimic LiveKit wrapping a 429 after exhausting retries (as seen in prod)."""
    cause = APIStatusError(
        "gemini llm: client error",
        status_code=429,
        body='{"error": {"status": "RESOURCE_EXHAUSTED"}}',
    )
    try:
        raise cause
    except APIStatusError as exc:
        outer = APIConnectionError("failed to generate LLM completion after 4 attempts")
        outer.__cause__ = exc
        return outer


async def _stream_ok():
    yield "Hello "
    yield "there."


async def _stream_rate_limited():
    yield "Let me check"  # some content may stream before the error
    raise APIStatusError(
        "gemini llm: client error",
        status_code=429,
        body='{"error": {"status": "RESOURCE_EXHAUSTED"}}',
    )


async def _stream_server_error():
    raise APIStatusError("gemini llm: server error", status_code=503, body="boom")
    yield  # unreachable; makes this an async generator, not a coroutine


async def _collect(agen) -> list:
    return [item async for item in agen]


def test_fallback_message_rate_limited_is_busy() -> None:
    exc = APIStatusError("x", status_code=429, body="RESOURCE_EXHAUSTED")
    msg = fallback_message(exc)
    assert "busy" in msg.lower()


def test_fallback_message_detects_wrapped_quota_error() -> None:
    # The real prod case: a generic APIConnectionError whose __cause__ is the 429.
    msg = fallback_message(_wrapped_quota_error())
    assert "busy" in msg.lower()


def test_fallback_message_other_error_is_generic() -> None:
    exc = APIStatusError("x", status_code=503, body="boom")
    msg = fallback_message(exc)
    assert "technical issue" in msg.lower()


@pytest.mark.asyncio
async def test_guard_passes_through_when_no_error() -> None:
    out = await _collect(guard_llm_stream(_stream_ok()))
    assert out == ["Hello ", "there."]


@pytest.mark.asyncio
async def test_guard_speaks_fallback_on_rate_limit() -> None:
    out = await _collect(guard_llm_stream(_stream_rate_limited()))
    # Prior content is preserved, and a spoken fallback is appended at the end.
    assert out[0] == "Let me check"
    assert "busy" in out[-1].lower()


@pytest.mark.asyncio
async def test_guard_speaks_fallback_on_server_error() -> None:
    out = await _collect(guard_llm_stream(_stream_server_error()))
    assert len(out) == 1
    assert "technical issue" in out[0].lower()
