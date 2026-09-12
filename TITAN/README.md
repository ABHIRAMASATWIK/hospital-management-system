# TITAN — AI Call Agent

A production-ready, modular **AI Call Agent** for an AI Automation Agency, built on
[LiveKit Agents](https://github.com/livekit/agents) for real-time voice.

- **LLM:** Google **Gemini 2.5 Flash**
- **STT:** **Sarvam AI** (multilingual, auto-detect / code-mixed — 22+ Indian languages + English)
- **TTS:** **Sarvam AI** (Bulbul)
- **Real-time engine:** LiveKit — native interruption/barge-in handling, turn detection, conversation history, tool calling
- **Noise cancellation:** ai-coustics background voice cancellation
- **Telephony:** Twilio/SIP as a modular seam, ready to connect
- **Structured call summaries** generated on hang-up (CRM-ready)

## Capabilities

- Natural, voice-first conversations (persona in `prompts/`)
- **Hospital receptionist booking flow** — asks one question at a time (department →
  doctor → name → phone → age → gender → symptoms → date → time), constrains choices to
  a real departments/doctors directory (`data/doctors.json`), checks the calendar, and
  auto-offers 3 nearby slots when the requested time is taken
- **Booking persistence** — every confirmed booking is written to **SQLite**
  (`data/titan.db`, primary datastore) and appended to an **Excel** report
  (`data/appointments.xlsx`, for staff). Both files auto-create on first booking
- Graceful interruption handling and turn-taking (LiveKit + Sarvam VAD)
- Persistent conversation history within a call
- Tool calling (`tools/registry.py`): departments/doctors lookup, availability +
  alternatives, booking, current time, lead capture (CRM seam), end call
- Structured outputs — a typed `CallSummary` (intent, key points, action items, sentiment, follow-up)
- Optional HTTP control plane (`api/`) for health, summaries, and leads

**Source-of-truth split:** Google Calendar owns the schedule (set `CALENDAR_BACKEND=google`),
SQLite owns application data, and Excel is a reporting mirror (a locked/open `.xlsx` never
blocks a booking).

## Architecture

All business logic lives in the `titan` package; `src/agent.py` is a thin LiveKit entrypoint.

```
src/
  agent.py                 # LiveKit entrypoint (thin): wires session + shutdown summary
  titan/
    config/settings.py     # Centralized, typed settings (pydantic-settings) — no hardcoded values
    prompts/persona.py     # TITAN system prompt (single source of truth)
    llm/gemini.py          # build_llm()  -> Gemini 2.5 Flash
    stt/sarvam_stt.py      # build_stt()  -> Sarvam STT (multilingual)
    tts/sarvam_tts.py      # build_tts()  -> Sarvam TTS
    tools/registry.py      # @function_tool set + testable impl helpers
    services/
      assistant.py         # Assistant(Agent): persona + tools
      session.py           # build_session(): LLM+STT+TTS+turn detection; noise-cancel room options
      summary.py           # generate_call_summary(): structured summary via Gemini
      call_store.py        # in-memory store + data models (CRM/DB seam)
      directory.py         # departments -> doctors directory (data/doctors.json seam)
      calendar.py          # calendar seam (memory | Google) + availability/alternatives
      booking.py           # booking orchestrator: validate -> schedule -> persist
      appointment_db.py    # SQLite datastore for confirmed bookings (primary)
      reporting.py         # Excel (.xlsx) report append (best-effort, staff-facing)
    telephony/twilio.py    # modular Twilio/SIP configuration seam
    api/app.py             # optional FastAPI control plane
    utils/logging.py       # centralized logging (+ Windows UTF-8 fix)
tests/                     # Gemini evals + offline unit tests
```

Each provider is built by a `build_*()` factory that reads from `config.settings`, so models,
languages, and credentials are configured entirely through environment variables.

## Setup

Requires **Python 3.12+** and the [`uv`](https://docs.astral.sh/uv/) package manager.

```bash
cd TITAN
uv sync                 # install core deps
# or, to also install the optional HTTP API:
uv sync --extra api
```

Copy the environment template and fill in your keys:

```bash
cp .env.example .env.local
```

Required variables (see `.env.example` for the full, documented list):

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `GOOGLE_API_KEY` — for Gemini (note: the Google plugin uses `GOOGLE_API_KEY`, **not** `GEMINI_API_KEY`)
- `SARVAM_API_KEY` — for Sarvam STT/TTS

## Run the agent

Talk to the agent directly in your terminal:

```bash
uv run python src/agent.py console
```

Run for a frontend or telephony:

```bash
uv run python src/agent.py dev      # development
uv run python src/agent.py start    # production
```

Optional control-plane API (needs `--extra api`):

```bash
uv run uvicorn titan.api.app:app --reload
# GET /health, GET /calls, GET /calls/{id}/summary, GET /leads
```

## Configuration knobs

Everything is centralized in `titan/config/settings.py` and overridable via env vars, e.g.
`LLM_MODEL`, `LLM_TEMPERATURE`, `STT_LANGUAGE`, `STT_MODE`, `TTS_TARGET_LANGUAGE`, `TTS_SPEAKER`,
`ASSISTANT_NAME`, `USER_NAME`, `LOG_LEVEL`.

Sarvam STT defaults to **multilingual auto-detect** (`STT_LANGUAGE=unknown`, `STT_MODE=codemix`).
Sarvam TTS synthesizes into one language; set `TTS_TARGET_LANGUAGE` (default `hi-IN`) per deployment.

## Telephony (Twilio) — connect later

Telephony is a modular seam (`titan/telephony/twilio.py`); the agent runs today without it.
When ready, connect a Twilio Elastic SIP Trunk to your LiveKit SIP endpoint, add the
`TWILIO_*` / `SIP_OUTBOUND_TRUNK_ID` values to `.env.local`, and route inbound calls to this
agent via a LiveKit dispatch rule. See the [LiveKit telephony guide](https://docs.livekit.io/telephony/).

## Testing

```bash
uv run pytest              # unit tests run offline; Gemini evals run only if GOOGLE_API_KEY is set
uv run ruff check          # lint
uv run ruff format --check # formatting
```

- `tests/test_tools.py`, `tests/test_summary.py` — fast, offline unit tests.
- `tests/test_agent.py` — live behavior evals judged by Gemini (auto-skipped without `GOOGLE_API_KEY`).

**End-to-end voice check:** set `GOOGLE_API_KEY`, `SARVAM_API_KEY`, and `LIVEKIT_*` in `.env.local`,
run `uv run python src/agent.py console`, speak, and confirm Sarvam STT → Gemini → Sarvam TTS and
barge-in work. Ending the session logs a structured `CallSummary`.

## Deploy

A production `Dockerfile` (Python 3.12) is included; it runs `uv run src/agent.py start`.
See the [LiveKit deployment guide](https://docs.livekit.io/deploy/agents/).

## License

MIT — see [LICENSE](LICENSE).
