"""Centralized, typed application configuration.

All environment-driven configuration lives here so that no other module needs
to read ``os.environ`` or hardcode provider names, model IDs, or languages.
Values are loaded (in order of precedence) from real environment variables,
then ``.env.local``, then ``.env``.

Usage::

    from titan.config import get_settings

    settings = get_settings()
    print(settings.llm_model)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = three parents up from this file
# (src/titan/config/settings.py -> src/titan/config -> src/titan -> src -> root).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _resolve_path(value: str) -> Path:
    """Resolve ``value`` against the project root unless it is absolute."""
    path = Path(value)
    return path if path.is_absolute() else _PROJECT_ROOT / path


class Settings(BaseSettings):
    """Strongly-typed settings for the TITAN AI Call Agent.

    Secrets (API keys) default to ``None`` so the package can be imported and
    unit-tested without a fully populated environment. The relevant provider
    plugin raises a clear error at runtime if a required key is missing.
    """

    model_config = SettingsConfigDict(
        # `.env.local` mirrors the LiveKit convention; `.env` is the fallback.
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Identity ---------------------------------------------------------
    agent_name: str = Field(
        default="my-agent",
        description="LiveKit dispatch name for this agent worker.",
    )
    assistant_name: str = Field(
        default="TITAN", description="Display name the assistant uses for itself."
    )
    user_name: str = Field(
        default="Abhi",
        description="Default name the assistant addresses the caller by.",
    )
    # Which persona the agent adopts. "titan" keeps the original personal
    # assistant; "receptionist" turns it into a hospital front-desk agent.
    persona: str = Field(default="receptionist")

    # --- Hospital / receptionist -----------------------------------------
    hospital_name: str = Field(
        default="ABC Hospital",
        description="Hospital name the receptionist persona greets callers with.",
    )
    # Path to the departments -> doctors directory (JSON). A relative path is
    # resolved against the project root at load time.
    doctors_file: str = Field(default="data/doctors.json")

    # --- LiveKit ----------------------------------------------------------
    livekit_url: str | None = Field(default=None)
    livekit_api_key: str | None = Field(default=None)
    livekit_api_secret: str | None = Field(default=None)

    # --- LLM: Google Gemini ----------------------------------------------
    google_api_key: str | None = Field(default=None)
    llm_model: str = Field(default="gemini-2.5-flash")
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    llm_top_p: float = Field(default=0.95, ge=0.0, le=1.0)
    # How hard to retry a failing LLM call before speaking the graceful fallback.
    # Kept low so a hard failure (e.g. quota 429) doesn't stall the call for long;
    # LiveKit's default of 3 retries x 2s adds ~6s of dead air on every failed turn.
    llm_max_retry: int = Field(default=1, ge=0)
    llm_retry_interval: float = Field(default=0.5, ge=0.0)

    # --- STT: Sarvam AI ---------------------------------------------------
    sarvam_api_key: str | None = Field(default=None)
    stt_model: str = Field(default="saaras:v3")
    # "unknown" enables Sarvam's automatic language detection.
    stt_language: str = Field(default="unknown")
    # "codemix" (saaras:v3) handles multilingual / code-mixed speech.
    stt_mode: str = Field(default="codemix")
    stt_sample_rate: int = Field(default=16000)
    stt_high_vad_sensitivity: bool = Field(default=True)

    # --- TTS: Sarvam AI ---------------------------------------------------
    tts_model: str = Field(default="bulbul:v3")
    # Sarvam TTS synthesizes into a single target language; change per deployment.
    tts_target_language: str = Field(default="hi-IN")
    # Must be compatible with tts_model; "anushka" is NOT valid for bulbul:v3.
    tts_speaker: str = Field(default="pooja")
    tts_speech_sample_rate: int = Field(default=22050)
    tts_pace: float = Field(default=1.0, gt=0.0)

    # --- Turn detection ---------------------------------------------------
    # Sarvam performs VAD internally; "stt" lets it drive end-of-turn signals.
    turn_detection: str = Field(default="stt")

    # --- Calendar / booking -----------------------------------------------
    # "memory" (default) books into an in-process calendar; "google" writes
    # real events to Google Calendar via a service account.
    calendar_backend: str = Field(default="memory")
    business_timezone: str = Field(
        default="Asia/Kolkata",
        description="IANA timezone appointments are booked in; naive times assume this.",
    )
    business_hours_start: int = Field(
        default=9, ge=0, le=23, description="First bookable hour (inclusive)."
    )
    business_hours_end: int = Field(
        default=18, ge=1, le=24, description="Last bookable hour (exclusive)."
    )
    default_appointment_minutes: int = Field(default=30, gt=0)
    # Required only when calendar_backend == "google".
    google_calendar_id: str | None = Field(default=None)
    google_service_account_file: str | None = Field(default=None)

    # --- Persistent storage (SQLite + Excel) ------------------------------
    # SQLite is the primary application database; Excel is a staff-facing
    # report. Relative paths resolve against the project root at load time.
    database_file: str = Field(default="data/titan.db")
    excel_file: str = Field(default="data/appointments.xlsx")
    # Number of alternative slots to offer when a requested time is taken.
    alternative_slot_count: int = Field(default=3, gt=0)
    # How far (minutes) around a taken slot to search for alternatives first.
    alternative_search_window_minutes: int = Field(default=120, gt=0)

    # --- Telephony (optional; wire later) ---------------------------------
    twilio_account_sid: str | None = Field(default=None)
    twilio_auth_token: str | None = Field(default=None)
    twilio_phone_number: str | None = Field(default=None)
    sip_outbound_trunk_id: str | None = Field(default=None)

    # --- Optional control-plane API ---------------------------------------
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    # Dashboard auth. jwt_secret MUST be set in production; the dev default
    # keeps a fresh checkout bootable for local work only.
    jwt_secret: str = Field(default="dev-only-change-me-0123456789abcdef")
    jwt_expiry_minutes: int = Field(default=720, gt=0)
    # Comma-separated list of allowed dashboard origins.
    cors_origins: str = Field(default="http://localhost:3000")

    # --- Observability ----------------------------------------------------
    log_level: str = Field(default="INFO")

    # --- Resolved filesystem paths ---------------------------------------
    @property
    def doctors_path(self) -> Path:
        """Absolute path to the departments/doctors directory file."""
        return _resolve_path(self.doctors_file)

    @property
    def database_path(self) -> Path:
        """Absolute path to the SQLite database file."""
        return _resolve_path(self.database_file)

    @property
    def excel_path(self) -> Path:
        """Absolute path to the Excel appointments workbook."""
        return _resolve_path(self.excel_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Cached so configuration is parsed once per process and shared everywhere.
    """
    return Settings()
