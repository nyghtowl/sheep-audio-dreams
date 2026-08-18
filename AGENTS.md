# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Two AI D&D characters (Lyra and Zara) play through an adventure autonomously using voice, orchestrated by Temporal workflows. Two implementations: a REST/Gradio demo and a streaming WebSocket/FastAPI demo.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in API keys

# Run (each demo needs Temporal running in a separate terminal)
temporal server start-dev

# REST demo (Gradio UI at localhost:7860)
python rest/app.py

# Streaming demo (FastAPI at localhost:8000)
python streaming/app.py

# Tests
pytest rest/tests/
pytest streaming/tests/
pytest rest/tests/test_agents.py::TestStripStageDirections  # single test class

# Lint (matches CI)
pylint $(git ls-files '*.py')

# Mock mode (REST only, no API keys needed)
MOCK_MODE=1 python rest/app.py
```

## Architecture

Two parallel demo directories (`rest/` and `streaming/`) with mirrored structure but different transport layers. Each has its own `app.py`, `agents.py`, `config.py`, `temporal_workflow.py`, and `tests/`.

### Core Pattern (shared by both demos)

1. **Config-driven agent routing** — `AgentConfig` dataclass in `config.py` defines each character's dialogue provider, voice, and prompts. `agents.py` routes to the correct API based on `agent.dialogue_provider`.
2. **Temporal embedded worker** — REST runs its Worker on a dedicated background event-loop thread; streaming starts its Worker in FastAPI's lifespan on the application event loop. UI interactions trigger Temporal Updates (not signals), which block until completion and return results directly.
3. **Audio lives outside Temporal** — `_last_audio[session_id]` dict holds audio bytes in-process. Temporal only serializes text state to keep the event log lean. On server restart between turns, the next character loses audio context and falls back to text-only.
4. **Activities auto-retry** — Temporal retry policy (500ms initial, 2x backoff, 3 attempts) handles transient API failures (429s, timeouts).

### REST Demo (`rest/`)

- `GameSession` class in `agents.py` manages state (history, turn index, audio context)
- Lyra: native audio via `gpt-4o-audio-preview` (dialogue + voice in one call)
- Zara: Gemini text generation + OpenAI TTS (two-step)
- DM narration: Claude Haiku or GPT-4o-mini after each d20 roll
- `_shared_state.py` exists to solve a double-import problem: `app.py` runs as `__main__`, so importing `_last_audio` from it would create a second dict instance the workflow never sees
- Workflow: `InteractiveGameWorkflow` with separate activities for turn audio and DM reaction

### Streaming Demo (`streaming/`)

- Lyra: OpenAI Realtime WebSocket (`gpt-4o-realtime-preview`)
- Zara: Gemini Live WebSocket with audio resampling (24kHz -> 16kHz via `_resample_24k_to_16k()`)
- Audio streams as PCM16 chunks through `asyncio.Queue` -> browser WebSocket. `None` sentinel signals end-of-turn.
- `_shared_state.py` holds the process-local queues and previous-turn audio shared by the FastAPI handler and Activity
- Zara has a 1.2MB audio safety cap (~25s) to prevent timeouts
- `streaming_turn_activity` heartbeats Temporal on every audio chunk to prevent timeout during long-running WebSocket streams
- Workflow: `StreamingGameWorkflow` with `get_turn_index` query for reconnection sync
- History context capped at last 10 turns

### Key Env Vars

| Key | REST | Streaming |
|-----|------|-----------|
| `OPENAI_API_KEY` | Lyra audio + Zara TTS | Lyra Realtime |
| `GEMINI_API_KEY` | Zara dialogue | Zara Live |
| `ANTHROPIC_API_KEY` | DM narration | Not needed |
| `ELEVENLABS_API_KEY` | Optional Zara TTS | Not used |
| `MOCK_MODE` | Scripted lines + silent audio | Not supported |

## Test Patterns

- REST tests use `MOCK_MODE=1` to avoid API calls; set before importing agents
- Streaming tests mock WebSocket connections with `MockRealtimeEvent` / `MockGeminiResponse` async context managers
- Streaming lifecycle tests verify import safety and neutral shared audio state
- Streaming lifecycle tests verify import safety and neutral shared audio state
- Async tests use `@pytest.mark.asyncio`
- `conftest.py` in each test dir adds the parent module to `sys.path`

## CI

GitHub Actions runs both test suites separately plus pylint on pushes and pull requests across Python 3.10, 3.11, 3.12.
