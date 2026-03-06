# REST Demo — `rest/`

Turn-by-turn REST approach: click **Next Turn**, wait for a full turn to complete, hear the audio. Each turn is a complete HTTP request/response cycle. This is the right starting point for understanding how voice agent pieces fit together before adding streaming complexity.

> **She Ships! — February 25, 2026** — This REST demo was the basis for the live demo at that talk ([slide deck PDF](../assets/SheShips_When_Voice_Agents_Roll_Initiative.pdf)). The repo has evolved since then:
> - The decorative dice animation became a real d20 mechanic — `workflow.random().randint(1, 20)` inside the Temporal workflow handler keeps rolls deterministic and replay-safe
> - A DM narration activity (`claude-haiku-4-5` or `gpt-4o-mini`) generates one sentence describing the roll outcome, displayed at the top of the *next* turn so it lands as a beat between characters after the audio has played

## Quick Start

```bash
# From the repo root — one-time setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in your API keys

# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — App
source .venv/bin/activate
python rest/app.py
```

Open http://localhost:7860. Watch the execution graph at http://localhost:8233.

## API Keys

| Key | Used for | Required? |
|-----|----------|-----------|
| `OPENAI_API_KEY` | Lyra's native voice (`gpt-4o-audio-preview`) + Zara's TTS (`tts-1`) | Yes |
| `GEMINI_API_KEY` | Zara's dialogue via `gemini-2.5-flash` | Yes (for Zara) |
| `ANTHROPIC_API_KEY` | DM narration (`claude-haiku-4-5`); Zara dialogue fallback if no Gemini key | Optional |
| `ELEVENLABS_API_KEY` | Alternative TTS for Zara | Optional |

**No keys?** Set `MOCK_MODE=1` in `.env` to run with scripted lines and silent audio.

## Voice Paths

| Character | Dialogue | Voice | Path |
|-----------|----------|-------|------|
| **Lyra** | `gpt-4o-audio-preview` | OpenAI native audio | Audio-in, Audio-out (one API call) |
| **Zara** | `gemini-2.5-flash` (Claude fallback) | OpenAI TTS `tts-1/nova` | Gemini text + OpenAI TTS (combined in one activity) |
| **DM** | `claude-haiku-4-5` or `gpt-4o-mini` | — | One short sentence per turn narrating the d20 roll outcome |

## Temporal Architecture

```
InteractiveGameWorkflow  (one per game session)
  ├── generate_turn_audio_activity   ← Lyra's turn  (dialogue + voice in one call)
  ├── generate_dm_reaction_activity  ← DM narrates Lyra's d20 roll
  ├── generate_turn_audio_activity   ← Zara's turn  (Gemini text + OpenAI TTS)
  ├── generate_dm_reaction_activity  ← DM narrates Zara's d20 roll
  └── ...
```

**Workflow lifecycle** — one `InteractiveGameWorkflow` per game session:
- Starts when the user clicks **Start Adventure**, stays alive until **Start Over** sends an `end_game` signal
- Each **Next Turn** click sends a Temporal Update — a request/response call that runs the character's activities and returns the result directly to the caller, no polling needed

**How a click becomes an activity** — Gradio is synchronous but Temporal is async:

```
Next Turn click
  → Gradio (sync) → asyncio.run_coroutine_threadsafe → background event loop
    → Temporal Update: execute_turn
      → generate_turn_audio_activity  (dialogue + audio)
      → generate_dm_reaction_activity (one-sentence DM narration)
    ← returns {dialogue, dm_text, roll}
  ← blocks on .result() ← Gradio displays result
```

The app runs a background thread with its own `asyncio` event loop dedicated to Temporal. When Next Turn fires, Gradio calls `asyncio.run_coroutine_threadsafe()` to hand the coroutine to that background loop, then blocks on `.result()` until the Update returns.

The Temporal server runs separately by design — if the app crashes mid-turn, restart it and the workflow resumes exactly where it left off.

**Audio stays out of workflow state** — the previous character's audio bytes live in an in-process dict (`_last_audio[session_id]`) rather than in Temporal's event log:
- Activities read the previous audio and write their own back to it directly
- The workflow only serializes text state: turn index, transcript history, session lifecycle
- If the server restarts between turns, the next character loses audio input and uses text context only — the conversation continues, just without the voice inflection as context

**Why `_shared_state.py` exists** — `_last_audio` needs to be the same dict object in both `app.py` and `temporal_workflow.py`. If `temporal_workflow.py` did `from app import _last_audio`, Python would re-import `app.py` as a fresh module (since `app.py` ran as `__main__`, not `app`) and create a second empty dict — the activity would write audio bytes to one dict while the UI reads from a different one. Putting the dict in a neutral `_shared_state.py` module avoids this: neither file is `__main__`, Python always returns the cached module, and both sides share the exact same dict.

**Keeping turns short** — dialogue length is controlled at two levels:
- Prompt instructions tell both characters to speak in one short sentence, no stage directions
- `max_output_tokens` caps the Gemini text generation step for Zara
- Lyra's audio output length is prompt-only since `gpt-4o-audio-preview` doesn't expose a direct audio duration limit

## Tests

No API keys needed — tests run in mock mode.

```bash
# From the repo root
source .venv/bin/activate
MOCK_MODE=1 python -m pytest rest/tests/ -v
```

## UI Features

- **Next Turn** — generate one turn manually. The previous turn's DM dice reaction (🎲) appears first, then the current character speaks — so the DM beat lands between characters rather than interrupting audio
- **Auto Run** — runs 12 turns automatically, duration-aware pacing
- **Start Over** — resets game state
