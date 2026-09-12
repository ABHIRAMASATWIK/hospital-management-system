"""Unit tests for transcript formatting and structured summary generation.

Runs offline: the fallback path is exercised when ``GOOGLE_API_KEY`` is unset.
"""

import pytest

from titan.config import Settings
from titan.services.call_store import CallSummary
from titan.services.summary import format_transcript, generate_call_summary


class _FakeItem:
    """Minimal stand-in for a LiveKit chat item."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _FakeHistory:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.items = items


def test_format_transcript_renders_roles_and_text() -> None:
    """Transcript formatting yields ROLE: text lines."""
    history = _FakeHistory(
        [_FakeItem("user", "Hello"), _FakeItem("assistant", "Hi there")]
    )
    transcript = format_transcript(history)
    assert transcript == "USER: Hello\nASSISTANT: Hi there"


def test_format_transcript_handles_list_content() -> None:
    """List-style content parts are joined into a single line."""
    history = _FakeHistory([_FakeItem("user", ["Hello", "world"])])
    assert format_transcript(history) == "USER: Hello world"


def test_format_transcript_empty() -> None:
    """An empty history yields an empty transcript."""
    assert format_transcript(_FakeHistory([])) == ""


@pytest.mark.asyncio
async def test_generate_summary_empty_transcript() -> None:
    """An empty transcript returns a well-formed 'no conversation' summary."""
    summary = await generate_call_summary("   ")
    assert isinstance(summary, CallSummary)
    assert summary.intent == "unknown"


@pytest.mark.asyncio
async def test_generate_summary_without_api_key() -> None:
    """With no API key configured, a graceful fallback summary is returned."""
    settings = Settings(google_api_key=None)
    summary = await generate_call_summary("USER: Hi\nASSISTANT: Hello", settings)
    assert isinstance(summary, CallSummary)
    assert summary.sentiment in ("positive", "neutral", "negative")
