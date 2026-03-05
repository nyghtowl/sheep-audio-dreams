# REST Demo — `rest/`

Turn-by-turn REST approach: click **Next Turn**, wait for a full turn to complete, hear the audio. Each turn is a complete HTTP request/response cycle. This is the right starting point for understanding how voice agent pieces fit together before adding streaming complexity.

## Quick Start

```bash
# From the repo root
python -m venv .venv
source .venv/bin/activate
pip install -r rest/requirements.txt
cp rest/.env.example .env  # fill in your API keys

# Terminal 1 — Temporal server
temporal server start-dev

# Terminal 2 — App
python rest/app.py
```

Open http://localhost:7860. Watch the execution graph at http://localhost:8233.

## API Keys

| Key | Used for | Required? |
|-----|----------|-----------|
| `OPENAI_API_KEY` | Lyra's native voice (`gpt-4o-audio-preview`) + Zara's TTS (`tts-1`) | Yes |
| `ANTHROPIC_API_KEY` | Zara's dialogue (`claude-sonnet-4-6`) | Yes (for Zara) |
| `GEMINI_API_KEY` | Zara's dialogue via `gemini-2.5-flash` | Optional, falls back to Claude |
| `ELEVENLABS_API_KEY` | Alternative TTS for Zara | Optional |

**No keys?** Set `MOCK_MODE=1` in `.env` to run with scripted lines and silent audio.

## Voice Paths

| Character | Dialogue | Voice | Path |
|-----------|----------|-------|------|
| **Lyra** | `gpt-4o-audio-preview` | OpenAI native audio | Audio-in, Audio-out (one API call) |
| **Zara** | `gemini-2.5-flash` (Claude fallback) | OpenAI TTS `tts-1/nova` | Text generation + separate TTS call |

## Temporal Architecture

```
InteractiveGameWorkflow  (one per game session)
  ├── generate_turn_audio_activity   ← Lyra's turn  (dialogue + voice in one call)
  ├── generate_dialogue_activity     ← Zara's turn  (text generation)
  │   synthesize_voice_activity      ←              (TTS)
  ├── generate_turn_audio_activity   ← Lyra's turn
  └── ...
```

The Temporal server runs separately by design — if the app crashes mid-turn, restart it and the workflow resumes exactly where it left off.

**Audio stays out of workflow state** — the previous character's audio bytes live in an in-process dict (`_last_audio[session_id]`) rather than in Temporal's event log. Activities read the previous audio and write their own back to it directly. The workflow only serializes text state: turn index, transcript history, session lifecycle. If the server restarts between turns, the next character loses the audio input and uses text context only — the conversation continues, just without the voice inflection as context.

**Keeping turns short** — dialogue length is controlled at the prompt level ("ONE short sentence only, no stage directions") and reinforced by `max_output_tokens` for the Gemini text generation step. Lyra's audio output length is prompt-only since `gpt-4o-audio-preview` doesn't expose a direct audio duration limit.

## UI Features

- **Next Turn** — generate one turn manually
- **Auto Run** — runs 12 turns automatically, duration-aware pacing
- **Start Over** — resets game state
