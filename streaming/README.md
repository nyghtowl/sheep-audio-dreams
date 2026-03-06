# Streaming Demo — `streaming/`

WebSocket streaming approach: audio flows continuously as it's generated, rather than waiting for a full turn to complete. Each character connects to a streaming speech API over WebSocket — Lyra to OpenAI Realtime, Zara to Gemini Live. The session runs as a durable Temporal workflow with per-turn heartbeats.

Lyra's turns start within ~1s. Zara's turns currently take 7–10s due to the connection-per-turn cost of Temporal's activity model (see [Temporal Architecture](#temporal-architecture) below). With connection pre-warming or a persistent session, Zara could reach the same latency — the streaming pipeline itself is ready for it.

## Quick Start

```bash
# From the repo root — one-time setup (shared with REST demo)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your API keys

# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — App
source .venv/bin/activate
python streaming/app.py
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

Both characters hear the previous character's actual audio, not just the transcript. How each handles it:

- **Lyra** — sends audio via OpenAI Realtime's `input_audio` content item (accepts 24 kHz PCM16 natively)
- **Zara** — sends audio via `send_realtime_input` with automatic VAD disabled; Lyra's 24 kHz output is resampled to 16 kHz (Gemini Live's expected input rate), and `activity_start`/`activity_end` bracket the audio so the model waits for the full clip before responding
- **Zara's first turn** — no prior audio exists, so `send_client_content` with text context is used instead; `send_client_content` and `send_realtime_input` cannot be interleaved in the same session, so on audio turns the conversation history is baked into the system instruction

Zara's output audio is passed to Lyra, capped at ~2s / 96 KB of PCM16 at 24 kHz.

**Keeping turns short** — dialogue length is controlled at two levels:
- Prompt instructions tell both characters to stop after 1–2 sentences
- Zara's audio receive loop has a hard byte cap (`MAX_AUDIO_BYTES`): once hit, the loop breaks and the turn ends regardless of whether the model has finished — native audio models don't expose a token or duration limit for audio output, so without a cap the receive loop has no reliable stopping point

## Temporal Architecture

```
StreamingGameWorkflow  (one per game session)
  ├── streaming_turn_activity   ← Lyra's turn  (WebSocket → audio queue → browser)
  ├── streaming_turn_activity   ← Zara's turn
  ├── streaming_turn_activity   ← Lyra's turn
  └── ...
```

**Workflow lifecycle** — one `StreamingGameWorkflow` per session:
- Starts on **Start Adventure**, stays alive until **Stop** or `MAX_TURNS` (12) is reached
- Each **Next Turn** click sends a Temporal Update that runs `streaming_turn_activity` and returns the transcript when the turn is done

**How a click becomes a stream** — FastAPI is natively async, so there's no sync/async bridge. Temporal's client calls are blocking, so they run in `asyncio.run_in_executor()` to avoid stalling FastAPI's event loop. Once the activity starts, three loops run concurrently:

```
Next Turn click (browser)
  → WebSocket message → FastAPI (async)
    → run_in_executor → Temporal Update: execute_turn
      → streaming_turn_activity opens WebSocket to OpenAI/Gemini
        → receives PCM16 chunk → puts in asyncio.Queue → heartbeat()
        → receives PCM16 chunk → puts in asyncio.Queue → heartbeat()
        → ... → puts None (end-of-turn sentinel)
      ← returns {transcript}
    ← FastAPI sends {"type": "turn_done"} to browser
  ↑ simultaneously:
  _forward_audio task reads queue → websocket.send_bytes() → browser Web Audio API plays chunks as they arrive
```

The `asyncio.Queue` is the handoff point between the three loops: the activity produces chunks, `_forward_audio` consumes them, the browser plays them. Temporal never sees the audio bytes — only the transcript returned at the end.

**Audio stays out of Temporal entirely** — neither the audio chunks nor the previous character's audio pass through Temporal's event log:
- The activity streams into the queue (which the WebSocket handler drains in real time)
- Each character's output audio is stored in an in-process dict (`_last_audio[session_id]`)
- The next activity reads from it at the start of its turn
- Temporal only serializes text: transcripts, turn index, session ID
- If the server restarts, the next turn falls back to text-only context — the conversation continues without the voice inflection as input

**Why `_last_audio` lives in `app.py` directly** — unlike the REST demo, streaming doesn't need a separate `_shared_state.py` module. The Temporal worker runs inside FastAPI's async event loop (same process, no background thread), so `from app import _last_audio` in the activity resolves to the already-loaded module — the same dict the WebSocket handler is writing to. No re-import problem, no neutral middleman needed.

**Heartbeats** — streaming activities run 10–30s per turn:
- Without heartbeats Temporal assumes the activity died and retries it
- Every audio chunk received triggers `activity.heartbeat()`
- `heartbeat_timeout` is 30s — if no chunk arrives for 30s, Temporal cancels and retries the activity with a fresh WebSocket connection

**Crash recovery** — `_get_or_start_workflow` checks for a `RUNNING` workflow before starting a new one. Kill `python app.py` mid-turn, restart it, reconnect the browser — the workflow resumes from the same turn. The audio queue is reset on reconnect so no stale chunks are replayed.

The Temporal server runs separately by design — it holds all workflow state independently of the app process.

## UI Features

- **Start Adventure** — starts the game and the Temporal workflow
- **Next Turn** — streams one character turn to the browser
- **Stop** — drains the audio queue, signals the workflow to end
