# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI characters play through a D&D adventure with no human involvement. Lyra, a half-elf ranger, and Zara, a tiefling sorceress, generate their own dialogue, roll dice, and advance the story — all spoken out loud to each other using native speech models. The entire session runs as a durable Temporal workflow.

The scenario: a wizard got polymorphed into a sheep by her evil apprentice and just crashed through the tavern door. Hit **Next Turn** and watch them figure it out.

![Campaign start](assets/sheep-dnd-start.png)

Today's voice agents aren't just answering questions — they're completing work:

- Latency has dropped below 500ms — faster than the average human reaction time, eliminating the awkward pauses that made early AI conversations feel unnatural
- These systems read the room: if a user sounds frustrated, the AI detects it and dynamically shifts tone with no human needed to intervene
- Voice is no longer a standalone channel — it's multimodal, multilingual, and increasingly paired with visual interfaces
- With tool use, agents don't just talk: they query systems, trigger workflows, and solve real problems in real time

Voice changes how people perceive AI. Audio adds an emotional dimension that text alone can't replicate — which means the bar for getting it right is higher. A few things to keep in mind:

- Models aren't game engines — they generate proposals, not authoritative truth. Every dice roll and state update is owned by the app code, not left to the model to improvise
- Autonomy doesn't automatically mean reliability: the more agents you coordinate, the more structure you need underneath them. That's what Temporal provides here

---

## What This Demo Shows

**REST demo** — turn-by-turn request/response model. Each character fully completes before the other responds. After each turn a Dungeon Master (Claude Haiku or GPT-4o-mini) narrates the d20 roll outcome in one punchy sentence. The code is straightforward, the Temporal execution graph is easy to read, and every step is visible. The right place to start before adding streaming complexity.

**Streaming demo** — WebSocket connections to native speech models. Characters begin speaking within <1s. The same Temporal workflow wraps the session, but audio delivery is out-of-band: the activity streams PCM16 chunks into an asyncio.Queue while Temporal tracks state and handles retries. Modern production agents add interruption handling (one character cuts the other off mid-sentence) using the same WebSocket APIs shown here.

**Voice** — both characters pass the previous character's actual audio as input, not just a text transcript. The models hear voice: tone, pacing, emotional cues. Keeping responses short requires two layers:
- Prompt-level instructions for both characters
- A hard byte cap on the streaming receive loop for Zara — native audio models don't expose a token limit for audio output, so without a cap the receive loop has no natural stopping point

**Temporal** — every turn runs as a durable activity in a single workflow per session. Crash the app mid-turn, restart it, and the workflow resumes exactly where it left off. See [How Temporal Works](#how-temporal-works) for what you'll see in the UI across both demos.

---

## Two Demos

| | REST (`rest/`) | Streaming (`streaming/`) |
|---|---|---|
| **First audio** | 5–10s per turn | Lyra ~1s / Zara 7–10s* |
| **Transport** | HTTP REST | WebSocket |
| **UI** | Gradio | FastAPI + HTML |
| **Lyra** | `gpt-4o-audio-preview` (REST) | `gpt-4o-realtime-preview` (WebSocket) |
| **Zara** | `gemini-2.5-flash` text + OpenAI TTS | `gemini-2.5-flash-native-audio` (WebSocket) |
| **Temporal** | `InteractiveGameWorkflow` | `StreamingGameWorkflow` |
| **Good for** | Understanding the pieces | Realistic production voice UX |

*Zara's latency reflects the connection-per-turn cost of Temporal's activity model — a new WebSocket handshake opens every turn. With connection pre-warming or a persistent session this could match Lyra's ~1s. See [How Temporal Works](#how-temporal-works).

---

## Setup

```bash
# From the repo root — one-time setup for both demos
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your API keys
```

---

## REST Demo

```bash
# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — Gradio app
source .venv/bin/activate
python rest/app.py
```

Open http://localhost:7860. Temporal UI at http://localhost:8233.

See [rest/README.md](rest/README.md) for full details.

---

## Streaming Demo

```bash
# Terminal 1 — Temporal server (shared with REST if both running)
temporal server start-dev

# Terminal 2 — FastAPI app
source .venv/bin/activate
python streaming/app.py
```

Open http://localhost:8000. Click **Start Adventure**, then **Next Turn** — Lyra's voice starts within a second.

Temporal UI at http://localhost:8233 shows `StreamingGameWorkflow` with `streaming_turn_activity` nodes. Kill the server mid-turn and restart — the workflow resumes.

See [streaming/README.md](streaming/README.md) for full details.

---

## API Keys

Copy `.env.example` and fill in:

| Key | REST | Streaming |
|-----|------|-----------|
| `OPENAI_API_KEY` | Lyra audio + Zara TTS | Lyra Realtime |
| `ANTHROPIC_API_KEY` | DM narration (Claude Haiku); Zara dialogue fallback | Not needed |
| `GEMINI_API_KEY` | Zara dialogue (primary) | Zara Live |
| `ELEVENLABS_API_KEY` | Optional Zara TTS | Not used |

**No keys?** Set `MOCK_MODE=1` in `.env` — REST demo only (streaming requires live API connections).

---

## How Temporal Works

The Temporal server runs separately from the app in both demos. The app embeds the worker (the code that executes activities), but the server holds all workflow state independently. Kill the app mid-turn, restart it, and the workflow picks up exactly where it left off — same turn, same state.

**REST demo** — each Next Turn click executes a Temporal Update. Distinct activity nodes appear in the UI:
- One for Lyra (dialogue + voice in a single native audio call)
- One for Zara (Gemini text + OpenAI TTS combined)
- One `generate_dm_reaction_activity` that narrates the d20 roll outcome

If an API call hits a 429 rate limit, Temporal retries with exponential backoff and the workflow never fails.

**Streaming demo** — the same Update pattern applies but the activity runs longer: it holds an open WebSocket connection and heartbeats Temporal on every audio chunk. The UI shows `streaming_turn_activity` nodes with timing that reflects the actual stream duration. Audio delivery is out-of-band (asyncio.Queue → browser), so Temporal tracks state and durability without serialising megabytes of audio.

**Streaming latency and the connection-per-turn tradeoff** — Lyra's turns start within ~1s. Zara's turns take 7–10s before the first audio arrives. The gap comes from a fundamental tension between Temporal's activity model and persistent WebSocket connections:

- Temporal activities are stateless and retryable — if one fails mid-turn, Temporal spins up a fresh execution
- A live WebSocket connection can't be serialized into Temporal's state, so the cleanest mapping is to open a fresh connection at the start of each activity and close it when the turn completes
- Every Zara turn pays the full cost of TLS handshake + session negotiation with Gemini Live (~2–4s), plus the model's own time-to-first-audio (~3–5s for the current preview model)

Persisting the connection across turns would hide that setup cost, but requires storing the session outside Temporal and manually re-implementing the retry semantics Temporal provides for free (health checks, reconnect on idle timeout, handling stale connections on retry). In this demo the tradeoff favors simplicity. The natural optimization for production is to pre-warm Zara's connection during Lyra's turn so the handshake is already complete when Zara's activity starts.

**Keeping Temporal state lean** — Temporal's event log is built for small, serializable data: text transcripts, turn index, session lifecycle. Audio bytes don't belong there — they'd bloat the history and slow replay. Both demos store the previous character's raw audio in an in-process dict (`_last_audio[session_id]`) inside the app:
- Activities read the previous audio and write their own back directly
- The workflow only tracks text state
- If the server restarts between turns, the next character loses audio context and falls back to text-only — the conversation continues, just without the voice inflection as input
- The natural production upgrade is to write audio to object storage and pass a URL through Temporal instead of the bytes

---

## The Voice Stack

Voice agents are built from layers. Most production systems stitch several of these together. The trend is toward fewer components — native conversational models are collapsing what used to be a three-step pipeline (STT → LLM → TTS) into a single end-to-end call. This demo sits at that inflection point: the REST demo uses a hybrid approach (native audio for Lyra, text + TTS for Zara), and the streaming demo moves both characters to fully native WebSocket models.

| Category | Role | What It Does | Leading Providers |
|----------|------|--------------|-------------------|
| **Speech-to-Text (STT)** | The Ears | Converts spoken audio into text for the system to process | AssemblyAI, Deepgram, OpenAI Whisper |
| **Conversational TTS** | The Fast Mouth | Ultra-low latency text-to-speech optimized for real-time responsiveness | Rime AI, Deepgram Aura, PlayHT |
| **Expressive TTS** | The Actor Mouth | High-fidelity speech focused on emotion, narration quality, and pronunciation | ElevenLabs, Murf AI, Resemble AI |
| **Speech-to-Speech (S2S)** | The Voice Changer | Takes raw audio as a blueprint and maps a new voice onto your exact pacing, pitch, and emotion | Supertone Shift, Respeecher, ElevenLabs Voice Changer |
| **Native Conversational AI** | The Fluid Companion | End-to-end models that process audio in and out natively — understand tone, handle interruptions, respond in real time | Gemini Live, OpenAI Advanced Voice Mode, Moshi, Hume AI |
| **Character Engines** | The Brain + Voice | Gives NPCs and digital avatars dynamic backstories, memory, and voice for games and immersive environments | Inworld AI, Replica Studios, Convai |
| **General Voice Orchestrators** | The Whole Package | Infrastructure that glues voice tools together and connects them to telecom networks for AI phone agents | Vapi, Retell AI, Bland AI |
| **Industry-Specific Orchestrators** | The Compliant Specialist | End-to-end agent platforms with strict guardrails and compliance (HIPAA, finance) for regulated industries | Gradient Labs, Tennr |

**Where this demo fits:** Lyra uses Native Conversational AI (OpenAI `gpt-4o-audio-preview` / `gpt-4o-realtime-preview`). Zara uses Expressive TTS (ElevenLabs/OpenAI `tts-1`) in the REST demo and Native Conversational AI (Gemini Live) in the streaming demo. Neither demo uses STT — the characters communicate directly through audio and text context.

![Campaign end](assets/sheep-dnd-end.png)

---

## Future Development Ideas

- **DM voice** — the Dungeon Master currently narrates via text only; the natural next step is routing DM lines through a TTS call (or a native audio model) so the d20 roll outcome is spoken aloud between character turns
- **Temporal native streaming** — Temporal is building native streaming support that would let the workflow push audio chunks directly to the caller without the current asyncio.Queue workaround; adopting it when available would simplify the architecture and remove the in-process shared state
- **Interruption handling** — production voice agents let one speaker cut the other off mid-sentence; both OpenAI Realtime and Gemini Live support this via VAD events, and the same Temporal activity structure can accommodate it by treating interruptions as early turn completions
- **Human-in-the-loop** — expose a text or voice input box so a player can speak as a third character; the workflow would route their input alongside Lyra and Zara's turns
- **Pre-warm Zara's connection** — open the Gemini Live WebSocket during Lyra's turn so the handshake cost is hidden; cuts Zara's time-to-first-audio from 7–10s to closer to Lyra's ~1s without changing the activity boundary model
- **Persistent memory** — store conversation history in an external store (e.g. a vector DB) so the characters remember events across sessions, not just within a single workflow run

---

## Resources

- [Temporal](https://temporal.io?utm_source=github&utm_medium=readme&utm_campaign=nyghtowl) — durable execution platform used to orchestrate both demos
- [temporalio/sdk-python](https://github.com/temporalio/sdk-python) — Python SDK used in this repo
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) — WebSocket speech model powering Lyra in the streaming demo
- [Gemini Live](https://ai.google.dev/gemini-api/docs/live) — WebSocket speech model powering Zara in the streaming demo
- [OpenAI Audio](https://platform.openai.com/docs/guides/audio) — `gpt-4o-audio-preview` used for Lyra in the REST demo
- [Gradio](https://www.gradio.app) — UI framework for the REST demo
- [FastAPI](https://fastapi.tiangolo.com) — web framework + WebSocket server for the streaming demo
- [When Voice Agents Roll Initiative (PDF)](assets/SheShips_When_Voice_Agents_Roll_Initiative.pdf) — slide deck
