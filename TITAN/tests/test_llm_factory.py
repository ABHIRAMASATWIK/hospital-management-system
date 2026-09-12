"""Offline tests for the LLM factory's provider dispatch."""

from __future__ import annotations

from titan.config import Settings
from titan.llm import build_llm


def _settings(model: str) -> Settings:
    return Settings(llm_model=model, sarvam_api_key="test-key")


def test_sarvam_model_builds_sarvam_llm():
    from livekit.plugins import sarvam

    llm = build_llm(_settings("sarvam-30b"))
    assert isinstance(llm, sarvam.LLM)


def test_gemini_model_builds_google_llm(monkeypatch):
    from livekit.plugins import google

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    llm = build_llm(_settings("gemini-2.5-flash"))
    assert isinstance(llm, google.LLM)
