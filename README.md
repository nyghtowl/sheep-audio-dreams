# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI agents play D&D characters in real-time. Dialogue by **Claude** or **OpenAI**; voices by **ElevenLabs** (and optionally OpenAI TTS).

## Quick Start

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a .env file (see .env.example). You need:
#    - ELEVENLABS_API_KEY (required for voice)
#    - For dialogue: OPENAI_API_KEY and/or ANTHROPIC_API_KEY (if both set, Claude is used)

# 4. Run the app
python app.py
```

Open http://localhost:7860 in your browser.

## How It Works

| Component | Tech |
|-----------|------|
| Character dialogue | **Claude** (if `ANTHROPIC_API_KEY` set) or **OpenAI GPT-4o** |
| Lyra's voice | ElevenLabs ("Aria") |
| Zara's voice | ElevenLabs ("Rachel") when using Claude; otherwise OpenAI TTS ("nova") |
| UI | Gradio |

**No OpenAI?** Set `ANTHROPIC_API_KEY` in `.env` to use Claude for dialogue and ElevenLabs for both voices.

## 3 Takeaways

1. **Personality via prompts** — System prompts give AI agents distinct characters
2. **Voice is ~5 lines of code** — ElevenLabs & OpenAI TTS make it trivial
3. **Agents can talk to each other** — Multi-agent orchestration in action
