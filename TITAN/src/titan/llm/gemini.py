"""Google Gemini large language model factory.

Wraps the official ``livekit-plugins-google`` integration so the rest of the
codebase depends on a stable ``build_llm()`` factory rather than the plugin's
constructor directly. Requires the ``GOOGLE_API_KEY`` environment variable.
"""

from __future__ import annotations

from livekit.plugins import google

from titan.config import Settings, get_settings


def build_llm(settings: Settings | None = None) -> google.LLM:
    """Build the configured Gemini LLM.

    Args:
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        A configured :class:`livekit.plugins.google.LLM` instance.
    """
    settings = settings or get_settings()
    return google.LLM(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
    )
