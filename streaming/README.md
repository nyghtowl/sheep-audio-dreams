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
| `GEMINI_API_KEY` | Zara's Gemini Live (`gemini-2.5-flash-native-audio-preview-12-2025`) | Yes |

## Voice Paths

| Character | API | Transport | Audio input |
|-----------|-----|-----------|-------------|
| **Lyra** | `gpt-4o-realtime-preview` | WebSocket | Text context + Zara's PCM16 audio at 24 kHz |
| **Zara** | `gemini-2.5-flash-native-audio-preview-12-2025` | WebSocket | Lyra's PCM16 audio resampled to 16 kHz (turns 2+) |

Both characters hear the previous character's actual audio, not just the transcript. Lyra sends audio via OpenAI Realtime's `input_audio` content item (accepts 24 kHz PCM16 natively). Zara sends audio via `send_realtime_input` with automatic VAD disabled — Lyra's 24 kHz output is resampled to 16 kHz (Gemini Live's expected input rate) before sending, and `activity_start`/`activity_end` bracket the audio so the model waits for the full clip before responding. Conversation history is baked into the system instruction for Zara's audio turns since `send_client_content` and `send_realtime_input` cannot be interleaved in the same session. On the first Zara turn (no prior audio) `send_client_content` with text context is used instead. Zara's output audio is passed to Lyra (capped at ~2s / 96 KB of PCM16 at 24 kHz before being sent as input to the next turn).

**Keeping turns short** — dialogue length is controlled at two levels. First, prompt instructions tell both characters to stop after 1–2 sentences. Second, Zara's audio receive loop has a hard byte cap (`MAX_AUDIO_BYTES = 384_000`): once that threshold is hit the loop breaks and the turn ends regardless of whether the model has finished. This second layer exists because native audio models don't expose a token or duration limit for their audio output — without a cap, the receive loop has no reliable stopping point if the model ignores the prompt constraint.

## Temporal Architecture

```
StreamingGameWorkflow  (one per game session)
  ├── streaming_turn_activity   ← Lyra's turn  (WebSocket → audio queue → browser)
  ├── streaming_turn_activity   ← Zara's turn
  ├── streaming_turn_activity   ← Lyra's turn
  └── ...
```

Audio delivery is out-of-band from Temporal: the activity streams PCM16 chunks into an `asyncio.Queue`, and the FastAPI WebSocket handler forwards them to the browser in real time. Temporal tracks state, handles retries, and ensures the session survives crashes.

**Audio stays out of Temporal entirely** — neither the audio chunks nor the previous character's audio pass through Temporal's event log. The activity streams into the queue (which the WebSocket handler drains), and each character's output audio is stored in an in-process dict (`_last_audio[session_id]`). The next activity reads from it at the start of its turn. Temporal only serializes text: transcripts, turn index, session ID. This keeps the event log small and replay fast. If the server restarts, the next turn falls back to text-only context — the conversation continues without the voice inflection as input.

Activities heartbeat on every audio chunk — if the connection is idle for 10s, Temporal marks the activity as failed and retries with a fresh WebSocket connection.

The Temporal server runs separately by design — kill `python app.py` mid-turn, restart it, and the workflow resumes.

## UI Features

- **Start Adventure** — starts the game and the Temporal workflow
- **Next Turn** — streams one character turn to the browser
- **Stop** — drains the audio queue, signals the workflow to end
