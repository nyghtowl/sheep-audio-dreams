# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI characters play through a D&D adventure with no human involvement. Lyra, a half-elf ranger, and Zara, a tiefling sorceress, generate their own dialogue, roll dice, and advance the story — all spoken out loud to each other using native speech models. The entire session runs as a durable Temporal workflow.

The scenario: a wizard got polymorphed into a sheep by her evil apprentice and just crashed through the tavern door. Hit **Next Turn** and watch them figure it out.

![Campaign start](assets/sheep-dnd-start.png)

Voice changes how people perceive AI. Audio adds an emotional dimension that text alone can't replicate, which means the bar for getting it right is higher. Models aren't game engines — they generate proposals, not authoritative truth. Every dice roll and state update is owned by the app code, not left to the model to improvise. And autonomy doesn't automatically mean reliability: the more agents you coordinate, the more structure you need underneath them. That's what Temporal provides here.

## Slides

[When Voice Agents Roll Initiative (PDF)](assets/SheShips_When_Voice_Agents_Roll_Initiative.pdf)

## Quick Start

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up API keys in .env (see .env.example)

# 4. Run the app
python app.py
```

Open http://localhost:7860 in your browser.

## API Keys

| Key | Used for | Required? |
|-----|----------|-----------|
| `OPENAI_API_KEY` | Lyra's native voice (`gpt-4o-audio-preview`) + Zara's TTS (`tts-1`) | Yes |
| `ANTHROPIC_API_KEY` | Zara's dialogue (`claude-sonnet-4-6`) | Yes (for Zara) |
| `GEMINI_API_KEY` | Zara's dialogue via `gemini-2.5-flash` (Gradio app only) | Optional, falls back to Claude |
| `ELEVENLABS_API_KEY` | Alternative TTS for Zara | Optional |

**No keys?** Set `MOCK_MODE=1` in `.env` to run with scripted lines and silent audio.

## How It Works

### Voice Paths

| Character | Dialogue | Voice | Path |
|-----------|----------|-------|------|
| **Lyra** (Half-Elf Ranger) | `gpt-4o-audio-preview` | OpenAI native audio | Audio-in, Audio-out (one API call) |
| **Zara** (Tiefling Sorceress) | `gemini-2.5-flash` (Claude fallback) | OpenAI TTS `tts-1/nova` | Text generation + separate TTS call |

**Native speech** means each character passes the previous character's actual audio as input so the model hears the voice, not just the text.

### UI Features

- **Next Turn** — generate one turn manually
- **Auto Run** — runs 12 turns automatically, duration-aware pacing between turns
- **Start Over** — resets game state and cancels any running auto-run

## Temporal

Temporal is embedded in the app — no separate worker process needed. Start the Temporal server, run the app, and every turn runs as a durable activity.

```bash
# Terminal 1 — Temporal server + Web UI
temporal server start-dev

# Terminal 2 — App (Gradio UI + embedded Temporal worker)
python app.py
```

Watch the execution graph at http://localhost:8233 as you play.

The Temporal server runs separately by design. The app embeds the worker (the code that executes activities), but the server holds all workflow state independently. This means you can kill `python app.py` mid-turn and the workflow survives — restart the app and it reconnects and picks up exactly where it left off. If both ran as one process, a crash would take the state with it and there would be nothing to recover.

### Architecture

```
InteractiveGameWorkflow  (one per game session)
  ├── generate_turn_audio_activity   ← Lyra's turn  (dialogue + voice in one call)
  ├── generate_dialogue_activity     ← Zara's turn  (text generation)
  │   synthesize_voice_activity      ←              (TTS)
  ├── generate_turn_audio_activity   ← Lyra's turn
  └── ...
```

One workflow per game. Activities appear in the execution graph as you click Next Turn. If the app crashes mid-activity, Temporal retries it automatically when the app restarts.

Zara gets two activities because her turn makes two separate API calls (text + TTS). Splitting them means each retries independently — if TTS fails after dialogue succeeds, only TTS is retried.

## A Note on Approach

This demo uses a REST-based, turn-by-turn model where each character fully completes before the other responds. That's intentional: it keeps the code readable and makes the Temporal execution graph easy to follow. Modern production voice agents use WebSocket streaming (OpenAI Realtime API, Gemini Live) for sub-second first audio and interruption handling. This repo is a good starting point for understanding the pieces before adding streaming.

## Key Takeaways

1. **Personality via prompts** — system prompts give AI agents distinct characters
2. **Native speech = richer interaction** — models hear voice, not just text; one API call does dialogue + TTS
3. **Durable orchestration** — Temporal retries failures automatically and survives worker crashes

![Campaign end](assets/sheep-dnd-end.png)
