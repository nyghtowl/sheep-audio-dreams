# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI characters play through a D&D adventure with no human involvement. Lyra, a half-elf ranger, and Zara, a tiefling sorceress, generate their own dialogue, roll dice, and advance the story — all spoken out loud to each other using native speech models. The entire session runs as a durable Temporal workflow.

The scenario: a wizard got polymorphed into a sheep by her evil apprentice and just crashed through the tavern door. Hit **Next Turn** and watch them figure it out.

![Campaign start](assets/sheep-dnd-start.png)

Voice changes how people perceive AI. Audio adds an emotional dimension that text alone can't replicate, which means the bar for getting it right is higher. Models aren't game engines — they generate proposals, not authoritative truth. Every dice roll and state update is owned by the app code, not left to the model to improvise. And autonomy doesn't automatically mean reliability: the more agents you coordinate, the more structure you need underneath them. That's what Temporal provides here.

## Slides

[When Voice Agents Roll Initiative (PDF)](assets/SheShips_When_Voice_Agents_Roll_Initiative.pdf)

---

## Two Demos

This repo contains two implementations of the same D&D scenario, showing the contrast between REST and streaming approaches.

| | REST (`rest/`) | Streaming (`streaming/`) |
|---|---|---|
| **First audio** | 5–10s per turn | < 1s |
| **Transport** | HTTP REST | WebSocket |
| **UI** | Gradio | FastAPI + HTML |
| **Lyra** | `gpt-4o-audio-preview` (REST) | `gpt-4o-realtime-preview` (WebSocket) |
| **Zara** | `gemini-2.5-flash` text + OpenAI TTS | `gemini-2.0-flash-live-001` (WebSocket) |
| **Temporal** | `InteractiveGameWorkflow` | `StreamingGameWorkflow` |
| **Good for** | Understanding the pieces | Realistic production voice UX |

Start with the REST demo. The code is straightforward, the Temporal execution graph is easy to read, and you can see every step. The streaming demo shows where the UX ends up once you add real-time audio delivery.

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

## How Temporal Works in Both Demos

The Temporal server runs separately from the app in both demos. This is intentional: the app embeds the worker (the code that executes activities), but the server holds all workflow state independently. Kill the app mid-turn, restart it, and the workflow picks up exactly where it left off.

**REST**: each turn is a Temporal Update that runs one or two activities (dialogue + TTS), returns the full audio, and completes.

**Streaming**: each turn is a Temporal Update that runs `streaming_turn_activity`. The activity opens a WebSocket to the AI API, streams audio chunks into an `asyncio.Queue`, and heartbeats Temporal on every chunk. The FastAPI WebSocket handler reads from that queue and sends bytes to the browser in real time. Audio delivery is out-of-band from Temporal — Temporal tracks state and handles retries; the queue handles real-time delivery.

---

## A Note on Approach

Both demos use a turn-by-turn model where each character fully completes before the other responds. Modern production voice agents add interruption handling (one character cuts off the other mid-sentence) using the same WebSocket APIs shown here. This repo is a good starting point for understanding the pieces before adding that complexity.

## Key Takeaways

1. **Personality via prompts** — system prompts give AI agents distinct characters
2. **Native speech = richer interaction** — models hear voice, not just text
3. **Durable orchestration** — Temporal retries failures automatically and survives worker crashes
4. **REST vs streaming** — REST makes the pieces legible; streaming makes the UX real

![Campaign end](assets/sheep-dnd-end.png)

---

## Resources

- [Temporal](https://temporal.io?utm_source=github&utm_medium=readme&utm_campaign=nyghtowl) — durable execution platform used to orchestrate both demos
- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) — WebSocket speech model powering Lyra in the streaming demo
- [Gemini Live](https://ai.google.dev/gemini-api/docs/live) — WebSocket speech model powering Zara in the streaming demo
- [OpenAI Audio](https://platform.openai.com/docs/guides/audio) — `gpt-4o-audio-preview` used for Lyra in the REST demo
- [Gradio](https://www.gradio.app) — UI framework for the REST demo
