"""Sarvam AI large language model factory.

Wraps the official ``livekit-plugins-sarvam`` LLM integration (an
OpenAI-compatible client against ``api.sarvam.ai``) so the rest of the codebase
depends on a stable factory rather than the plugin's constructor directly.
Requires the ``SARVAM_API_KEY`` environment variable.
"""

from __future__ import annotations

from livekit.plugins import sarvam

from titan.config import Settings, get_settings


def build_sarvam_llm(settings: Settings | None = None) -> sarvam.LLM:
    """Build the configured Sarvam LLM.

    Args:
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        A configured :class:`livekit.plugins.sarvam.LLM` instance.
    """
    settings = settings or get_settings()
    kwargs: dict = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "top_p": settings.llm_top_p,
        # Sarvam chat models are reasoning models; without this they think for
        # ~30s before replying, which is unusable for voice. "low" is the
        # minimum the API accepts (still ~7s to first token).
        "reasoning_effort": "low",
    }
    if settings.sarvam_api_key:
        kwargs["api_key"] = settings.sarvam_api_key
    return sarvam.LLM(**kwargs)
