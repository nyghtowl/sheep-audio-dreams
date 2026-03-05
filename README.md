# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI characters play through a D&D adventure with no human involvement. Lyra, a half-elf ranger, and Zara, a tiefling sorceress, generate their own dialogue, roll dice, and advance the story — all spoken out loud to each other using native speech models. The entire session runs as a durable Temporal workflow.

The scenario: a wizard got polymorphed into a sheep by her evil apprentice and just crashed through the tavern door. Hit **Next Turn** and watch them figure it out.

![Campaign start](assets/sheep-dnd-start.png)

Today's voice agents aren't just answering questions — they're completing work. Agents are already conducting live outbound sales calls, updating CRM databases, and booking appointments without human intervention. Latency has dropped below 500 milliseconds, faster than the average human reaction time, eliminating the awkward pauses that made early AI conversations feel unnatural. These systems read the room: if a user sounds frustrated, the AI detects it and dynamically shifts tone with no human needed to intervene. Voice is also no longer a standalone channel — it's multimodal, multilingual, and increasingly paired with visual interfaces. And with tool use, agents don't just talk: they query systems, trigger workflows, and solve real problems in real time.

Voice changes how people perceive AI. Audio adds an emotional dimension that text alone can't replicate, which means the bar for getting it right is higher. Models aren't game engines — they generate proposals, not authoritative truth. Every dice roll and state update is owned by the app code, not left to the model to improvise. And autonomy doesn't automatically mean reliability: the more agents you coordinate, the more structure you need underneath them. That's what Temporal provides here.

---

## What This Demo Shows

**REST demo** — turn-by-turn request/response model. Each character fully completes before the other responds. The code is straightforward, the Temporal execution graph is easy to read, and you can see every step. The right place to start before adding streaming complexity.

**Streaming demo** — WebSocket connections to native speech models. Characters begin speaking within <1s. The same Temporal workflow wraps the session, but audio delivery is out-of-band: the activity streams PCM16 chunks into an asyncio.Queue while Temporal tracks state and handles retries. Both demos use a turn-by-turn model — modern production agents add interruption handling (one character cuts the other off mid-sentence) using the same WebSocket APIs shown here.

**Voice** — both characters pass the previous character's actual audio as input, not just a text transcript. The models hear voice: tone, pacing, emotional cues. For Lyra, one API call handles both dialogue generation and speech synthesis. This is what makes native speech feel different from text-to-speech bolted on top.

**Temporal** — every turn runs as a durable activity in a single workflow per session. Crash the app mid-turn, restart it, and the workflow resumes exactly where it left off. See [How Temporal Works](#how-temporal-works) for what you'll see in the UI across both demos.

---

## Two Demos

| | REST (`rest/`) | Streaming (`streaming/`) |
|---|---|---|
| **First audio** | 5–10s per turn | < 1s |
| **Transport** | HTTP REST | WebSocket |
| **UI** | Gradio | FastAPI + HTML |
| **Lyra** | `gpt-4o-audio-preview` (REST) | `gpt-4o-realtime-preview` (WebSocket) |
| **Zara** | `gemini-2.5-flash` text + OpenAI TTS | `gemini-2.0-flash-live-001` (WebSocket) |
| **Temporal** | `InteractiveGameWorkflow` | `StreamingGameWorkflow` |
| **Good for** | Understanding the pieces | Realistic production voice UX |

---

## REST Demo

```bash
cd rest
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example ../.env  # fill in your API keys

# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — Gradio app
source .venv/bin/activate
python app.py
```

Open http://localhost:7860. Temporal UI at http://localhost:8233.

See [rest/README.md](rest/README.md) for full details.

---

## Streaming Demo

```bash
cd streaming
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../rest/.env.example ../.env  # fill in your API keys

# Terminal 1 — Temporal server (shared with REST if both running)
temporal server start-dev

# Terminal 2 — FastAPI app
source .venv/bin/activate
python app.py
```

Open http://localhost:8000. Click **Start Adventure**, then **Next Turn** — Lyra's voice starts within a second.

Temporal UI at http://localhost:8233 shows `StreamingGameWorkflow` with `streaming_turn_activity` nodes. Kill the server mid-turn and restart — the workflow resumes.

See [streaming/README.md](streaming/README.md) for full details.

---

## API Keys

Copy `.env.example` from `rest/` and fill in:

| Key | REST | Streaming |
|-----|------|-----------|
| `OPENAI_API_KEY` | Lyra audio + Zara TTS | Lyra Realtime |
| `ANTHROPIC_API_KEY` | Zara dialogue fallback | Not needed |
| `GEMINI_API_KEY` | Zara dialogue | Zara Live |
| `ELEVENLABS_API_KEY` | Optional Zara TTS | Not used |

**No keys?** Set `MOCK_MODE=1` in `.env` — REST demo only (streaming requires live API connections).

---

## How Temporal Works

The Temporal server runs separately from the app in both demos. The app embeds the worker (the code that executes activities), but the server holds all workflow state independently. Kill the app mid-turn, restart it, and the workflow picks up exactly where it left off — same turn, same state.

In the **REST demo**, each Next Turn click executes a Temporal Update. You'll see distinct activity nodes in the UI: one for Lyra (dialogue + voice in a single native audio call) and two for Zara (text generation, then TTS) — splitting them means each retries independently if one fails. If an API call hits a 429 rate limit, Temporal retries with exponential backoff and the workflow never fails.

In the **streaming demo**, the same Update pattern applies but the activity runs longer — it holds an open WebSocket connection and heartbeats Temporal on every audio chunk. The UI shows `streaming_turn_activity` nodes with timing that reflects the actual stream duration. Audio delivery is out-of-band (asyncio.Queue → browser), so Temporal tracks state and durability without serialising megabytes of audio.

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

## Resources

- [Temporal](https://temporal.io?utm_source=github&utm_medium=readme&utm_campaign=nyghtowl) — durable execution platform used to orchestrate both demos
- [temporalio/sdk-python](https://github.com/temporalio/sdk-python) — Python SDK used in this repo
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) — WebSocket speech model powering Lyra in the streaming demo
- [Gemini Live](https://ai.google.dev/gemini-api/docs/live) — WebSocket speech model powering Zara in the streaming demo
- [OpenAI Audio](https://platform.openai.com/docs/guides/audio) — `gpt-4o-audio-preview` used for Lyra in the REST demo
- [Gradio](https://www.gradio.app) — UI framework for the REST demo
- [FastAPI](https://fastapi.tiangolo.com) — web framework + WebSocket server for the streaming demo
- [When Voice Agents Roll Initiative (PDF)](assets/SheShips_When_Voice_Agents_Roll_Initiative.pdf) — slide deck
