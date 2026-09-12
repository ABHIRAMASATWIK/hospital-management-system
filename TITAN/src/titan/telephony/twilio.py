"""Modular Twilio / SIP telephony seam.

This module is intentionally a thin, dependency-free configuration seam so the
agent runs today (web/console) and can be connected to real phone calls later
without touching the core pipeline.

How telephony works with LiveKit
--------------------------------
LiveKit connects to phone networks through **SIP**. A typical Twilio setup is:

1. Buy a phone number in Twilio and create an **Elastic SIP Trunk**.
2. Point the trunk's origination/termination at your LiveKit SIP endpoint and
   create inbound/outbound trunks with the LiveKit CLI (``lk sip ...``).
3. Route inbound calls to a dispatch rule that starts this agent
   (``agent_name`` from settings) in a room; the same audio pipeline
   (Sarvam STT -> Gemini -> Sarvam TTS) then serves the call unchanged.

The credentials below are read from configuration but are **optional** — the
agent does not require them to run. When you are ready to enable calling, fill
in the Twilio/SIP values in your ``.env.local`` and follow the LiveKit
telephony guide: https://docs.livekit.io/telephony/
"""

from __future__ import annotations

from dataclasses import dataclass

from titan.config import Settings, get_settings


@dataclass(frozen=True)
class TwilioConfig:
    """Resolved Twilio / SIP configuration for telephony."""

    account_sid: str | None
    auth_token: str | None
    phone_number: str | None
    sip_outbound_trunk_id: str | None

    @property
    def is_configured(self) -> bool:
        """True when the minimum credentials for calling are present."""
        return bool(self.account_sid and self.auth_token and self.phone_number)


def get_twilio_config(settings: Settings | None = None) -> TwilioConfig:
    """Build a :class:`TwilioConfig` from application settings."""
    settings = settings or get_settings()
    return TwilioConfig(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        phone_number=settings.twilio_phone_number,
        sip_outbound_trunk_id=settings.sip_outbound_trunk_id,
    )


def is_telephony_configured(settings: Settings | None = None) -> bool:
    """Return True when telephony credentials are configured."""
    return get_twilio_config(settings).is_configured
