"""Sarvam AI text-to-speech factory.

Uses the official ``livekit-plugins-sarvam`` integration with the ``bulbul:v3``
model. Sarvam synthesizes into a single target language, so the output language
and speaker are configurable per deployment. Requires the ``SARVAM_API_KEY``
environment variable.
"""

from __future__ import annotations

from livekit.plugins import sarvam

from titan.config import Settings, get_settings


def build_tts(settings: Settings | None = None) -> sarvam.TTS:
    """Build the configured Sarvam TTS engine.

    Args:
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        A configured :class:`livekit.plugins.sarvam.TTS` instance.
    """
    settings = settings or get_settings()
    return sarvam.TTS(
        target_language_code=settings.tts_target_language,
        model=settings.tts_model,
        speaker=settings.tts_speaker,
        speech_sample_rate=settings.tts_speech_sample_rate,
        pace=settings.tts_pace,
    )
