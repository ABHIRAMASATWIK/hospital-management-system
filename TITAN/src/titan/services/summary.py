"""Conversation summary generation.

At the end of a call, the transcript is condensed into a structured
:class:`~titan.services.call_store.CallSummary` using Gemini with a constrained
response schema. Structured output makes summaries safe to feed into CRMs,
analytics, and dashboards.

The generation is defensive: if no API key is configured or the model call
fails, a minimal fallback summary is returned so call teardown never crashes.
"""

from __future__ import annotations

from typing import Any

from titan.config import Settings, get_settings
from titan.services.call_store import CallSummary
from titan.utils import get_logger

logger = get_logger("titan.summary")

_SYSTEM_INSTRUCTION = (
    "You are a call-analytics assistant. Given a voice conversation transcript, "
    "produce a concise, accurate structured summary. Be objective, capture the "
    "caller's true intent, and only mark follow_up_required when a human action "
    "is genuinely needed."
)


def format_transcript(history: Any) -> str:
    """Render a LiveKit chat history into a plain-text transcript.

    Defensive against differences in the ``ChatContext`` API across LiveKit
    versions: it inspects common attributes and extracts role + text.

    Args:
        history: A LiveKit ``ChatContext`` (typically ``session.history``).

    Returns:
        A newline-separated ``ROLE: text`` transcript, or an empty string.
    """
    items = getattr(history, "items", None) or getattr(history, "messages", None) or []
    lines: list[str] = []
    for item in items:
        role = getattr(item, "role", None) or "unknown"
        content = getattr(item, "content", None)
        if content is None:
            content = getattr(item, "text", "")
        if isinstance(content, (list, tuple)):
            content = " ".join(str(part) for part in content if part)
        text = str(content).strip()
        if text:
            lines.append(f"{str(role).upper()}: {text}")
    return "\n".join(lines)


async def generate_call_summary(
    transcript: str, settings: Settings | None = None
) -> CallSummary:
    """Generate a structured summary of a conversation transcript.

    Args:
        transcript: The plain-text conversation transcript.
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        A :class:`CallSummary`. Falls back to a minimal summary on any error.
    """
    settings = settings or get_settings()

    if not transcript.strip():
        return CallSummary(intent="unknown", summary="No conversation took place.")

    if not settings.google_api_key:
        logger.warning("GOOGLE_API_KEY not set; returning fallback summary.")
        return CallSummary(
            intent="unknown", summary="Summary unavailable (no API key)."
        )

    try:
        # Imported lazily so importing this module never requires the SDK.
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.google_api_key)
        # Summaries always go through the Gemini SDK; when the conversation
        # LLM is a non-Gemini model (e.g. sarvam-*), fall back to a Gemini
        # model here instead of passing an unknown name to the API.
        model = (
            settings.llm_model
            if settings.llm_model.startswith("gemini")
            else "gemini-2.5-flash"
        )
        response = await client.aio.models.generate_content(
            model=model,
            contents=[f"Conversation transcript:\n\n{transcript}"],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=CallSummary,
                temperature=0.2,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, CallSummary):
            return parsed
        # Fall back to parsing the raw JSON text if `.parsed` is unavailable.
        if response.text:
            return CallSummary.model_validate_json(response.text)
    except Exception as exc:
        logger.warning("Failed to generate structured summary: %s", exc)

    return CallSummary(intent="unknown", summary="Summary generation failed.")
