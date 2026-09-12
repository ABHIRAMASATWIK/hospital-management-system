"""Sarvam AI speech-to-text factory.

Uses the official ``livekit-plugins-sarvam`` integration. Defaults to
multilingual auto-detection (``language="unknown"``) with code-mixed handling
(``mode="codemix"``) on the ``saaras:v3`` model. ``flush_signal=True`` lets the
plugin emit start/end-of-speech events for reliable turn-taking. Requires the
``SARVAM_API_KEY`` environment variable.
"""

from __future__ import annotations

from livekit.plugins import sarvam

from titan.config import Settings, get_settings


def build_stt(settings: Settings | None = None) -> sarvam.STT:
    """Build the configured Sarvam STT engine.

    Args:
        settings: Optional settings override; defaults to the cached settings.

    Returns:
        A configured :class:`livekit.plugins.sarvam.STT` instance.
    """
    settings = settings or get_settings()
    return sarvam.STT(
        language=settings.stt_language,
        model=settings.stt_model,
        mode=settings.stt_mode,
        sample_rate=settings.stt_sample_rate,
        high_vad_sensitivity=settings.stt_high_vad_sensitivity,
        # Emit start/end-of-speech signals so turn detection is reliable.
        flush_signal=True,
    )
