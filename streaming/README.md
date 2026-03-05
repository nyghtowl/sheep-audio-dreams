# Streaming Demo — `streaming/`

WebSocket streaming approach: characters begin speaking within <1s and audio flows continuously as it's generated. Each character connects to a streaming speech API over WebSocket — Lyra to OpenAI Realtime, Zara to Gemini Live. The session runs as a durable Temporal workflow with per-turn heartbeats.

## Quick Start

```bash
# From the repo root
cd streaming
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../rest/.env.example ../.env  # fill in your API keys

# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — App
source .venv/bin/activate
python app.py
```

Open http://localhost:8000. Click **Start Adventure**, then **Next Turn** — Lyra's voice starts within a second. Watch the execution graph at http://localhost:8233.

## API Keys

| Key | Used for | Required? |
|-----|----------|-----------|
| `OPENAI_API_KEY` | Lyra's Realtime API (`gpt-4o-realtime-preview`) | Yes |
| `GEMINI_API_KEY` | Zara's Gemini Live (`gemini-2.0-flash-live-001`) | Yes |

## Voice Paths

| Character | API | Transport | Path |
|-----------|-----|-----------|------|
| **Lyra** | `gpt-4o-realtime-preview` | WebSocket | Audio-in, audio-out — hears previous turn |
| **Zara** | `gemini-2.0-flash-live-001` | WebSocket | Audio-in, audio-out — hears previous turn |

Both characters receive the previous character's audio as input — they hear the actual voice, not just a text transcript.

## Temporal Architecture

```
StreamingGameWorkflow  (one per game session)
  ├── streaming_turn_activity   ← Lyra's turn  (WebSocket → audio queue → browser)
  ├── streaming_turn_activity   ← Zara's turn
  ├── streaming_turn_activity   ← Lyra's turn
  └── ...
```

Audio delivery is out-of-band from Temporal: the activity streams PCM16 chunks into an `asyncio.Queue`, and the FastAPI WebSocket handler forwards them to the browser in real time. Temporal tracks state, handles retries, and ensures the session survives crashes.

Activities heartbeat on every audio chunk — if the connection is idle for 10s, Temporal marks the activity as failed and retries with a fresh WebSocket connection.

The Temporal server runs separately by design — kill `python app.py` mid-turn, restart it, and the workflow resumes.

## UI Features

- **Start Adventure** — starts the game and the Temporal workflow
- **Next Turn** — streams one character turn to the browser
- **Stop** — drains the audio queue, signals the workflow to end
