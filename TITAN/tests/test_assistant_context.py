"""Offline tests: BusinessContext overrides flow into the assistant + session.

These cover the dashboard → agent wiring (greeting/prompt/voice/language)
without any network calls; the live conversational evals stay in
``test_agent.py``.
"""

from __future__ import annotations

from titan.config import get_settings
from titan.services.assistant import Assistant
from titan.services.session import apply_context_to_settings
from titan.services.tenant import BusinessContext


def _context(**overrides) -> BusinessContext:
    base = {
        "business_id": 7,
        "business_name": "Sunrise Clinic",
        "slug": "sunrise",
        "timezone": "Asia/Kolkata",
        "business_hours": (9, 18),
        "status": "active",
        "assistant_name": "Aarohi",
        "greeting": None,
        "prompt_override": None,
        "voice": "meera",
        "language": "en-IN",
    }
    base.update(overrides)
    return BusinessContext(**base)


def test_assistant_without_context_keeps_static_settings():
    agent = Assistant()
    settings = get_settings()
    assert settings.assistant_name in agent.instructions
    assert settings.hospital_name in agent.instructions


def test_context_overrides_name_and_business_name():
    agent = Assistant(context=_context())
    assert "Aarohi" in agent.instructions
    assert "Sunrise Clinic" in agent.instructions


def test_context_greeting_is_injected():
    ctx = _context(greeting="Namaste! Welcome to Sunrise Clinic.")
    agent = Assistant(context=ctx)
    assert "Namaste! Welcome to Sunrise Clinic." in agent.instructions


def test_context_prompt_override_replaces_body_but_keeps_voice_rules():
    ctx = _context(prompt_override="You are a pirate receptionist.")
    agent = Assistant(context=ctx)
    assert "You are a pirate receptionist." in agent.instructions
    # Voice output rules must survive a custom prompt.
    assert "text-to-speech" in agent.instructions


def test_apply_context_to_settings_overrides_tts():
    settings = get_settings()
    updated = apply_context_to_settings(settings, _context())
    assert updated.tts_speaker == "meera"
    assert updated.tts_target_language == "en-IN"
    # Original settings object is untouched (cached singleton safety).
    assert settings.tts_speaker != "meera" or settings is not updated


def test_apply_context_to_settings_without_context_is_identity():
    settings = get_settings()
    assert apply_context_to_settings(settings, None) is settings
