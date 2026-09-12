"""TITAN — a production-ready, modular AI Call Agent.

The package is organized into single-responsibility subpackages:

- ``config``    — centralized, typed settings loaded from the environment.
- ``prompts``   — system prompts / persona definitions.
- ``llm``       — large language model factory (Google Gemini 2.5 Flash).
- ``stt``       — speech-to-text factory (Sarvam AI).
- ``tts``       — text-to-speech factory (Sarvam AI).
- ``tools``     — LLM-callable function tools.
- ``services``  — agent, session wiring, summaries, and the call store.
- ``telephony`` — modular Twilio / SIP integration seam.
- ``api``       — optional FastAPI control plane (health, summaries, CRM hooks).
- ``utils``     — cross-cutting helpers (logging).

The LiveKit entrypoint lives in ``src/agent.py`` and delegates all business
logic to this package.
"""

__version__ = "2.0.0"
