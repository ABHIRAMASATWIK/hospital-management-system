"""Large language model factory.

Dispatches on ``settings.llm_model``: ``sarvam-*`` models build the Sarvam LLM,
anything else builds Google Gemini (the original default).

Both provider modules are imported eagerly: LiveKit plugins must register on
the main thread at import time, and ``build_llm`` may run inside a job thread.
"""

from __future__ import annotations

from titan.config import Settings, get_settings
from titan.llm.gemini import build_llm as build_gemini_llm
from titan.llm.sarvam_llm import build_sarvam_llm


def build_llm(settings: Settings | None = None):
    """Build the configured LLM, choosing the provider from the model name."""
    settings = settings or get_settings()
    if settings.llm_model.startswith("sarvam-"):
        return build_sarvam_llm(settings)
    return build_gemini_llm(settings)


__all__ = ["build_llm"]
