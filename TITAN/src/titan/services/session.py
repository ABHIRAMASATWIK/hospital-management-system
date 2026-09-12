"""Voice pipeline (AgentSession) wiring.

Assembles the LLM, STT, and TTS engines into a LiveKit :class:`AgentSession`
and configures turn detection. Also builds the room input options that enable
background noise cancellation. This is the single place the real-time pipeline
is composed.
"""

from __future__ import annotations

from livekit.agents import AgentSession, APIConnectOptions, room_io
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import ai_coustics

from titan.config import Settings, get_settings
from titan.llm import build_llm
from titan.services.tenant import BusinessContext
from titan.stt import build_stt
from titan.tts import build_tts


def apply_context_to_settings(
    settings: Settings, context: BusinessContext | None
) -> Settings:
    """Return settings with the tenant's voice/language applied.

    A copy is returned so the cached settings singleton is never mutated;
    with no context the settings pass through unchanged.
    """
    if context is None:
        return settings
    return settings.model_copy(
        update={
            "tts_speaker": context.voice,
            "tts_target_language": context.language,
        }
    )


def build_session(
    settings: Settings | None = None, context: BusinessContext | None = None
) -> AgentSession:
    """Build the voice :class:`AgentSession`.

    Wires Gemini (LLM) + Sarvam (STT/TTS). Turn detection is delegated to the
    STT engine (Sarvam performs VAD internally), so no external VAD is attached.

    Args:
        settings: Optional settings override; defaults to the cached settings.
        context: Optional tenant context; its voice/language override the
            static TTS settings for this session.

    Returns:
        A configured :class:`livekit.agents.AgentSession`.
    """
    settings = apply_context_to_settings(settings or get_settings(), context)
    # Fail fast on a failing LLM call so the graceful spoken fallback (see
    # titan.services.assistant) kicks in quickly instead of after ~6s of retries.
    conn_options = SessionConnectOptions(
        llm_conn_options=APIConnectOptions(
            max_retry=settings.llm_max_retry,
            retry_interval=settings.llm_retry_interval,
        ),
    )
    return AgentSession(
        llm=build_llm(settings),
        stt=build_stt(settings),
        tts=build_tts(settings),
        turn_detection=settings.turn_detection,
        conn_options=conn_options,
    )


def build_room_options() -> room_io.RoomOptions:
    """Build room I/O options with background noise cancellation enabled."""
    return room_io.RoomOptions(
        audio_input=room_io.AudioInputOptions(
            noise_cancellation=ai_coustics.audio_enhancement(
                model=ai_coustics.EnhancerModel.QUAIL_VF_S
            ),
        ),
    )
