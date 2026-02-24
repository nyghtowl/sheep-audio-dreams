# ⚔️ D&D Voice Agents Demo — The Wild Sheep Chase

Two AI agents play D&D characters in real-time. One voiced by **ElevenLabs**, the other by **OpenAI TTS**.

## Quick Start

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add your API keys to .env
cp .env .env  # Edit with your real keys

# 4. Run the app
python app.py
```

Open http://localhost:7860 in your browser.

## How It Works

| Component | Tech |
|-----------|------|
| Character dialogue | OpenAI GPT-4o |
| Lyra's voice | ElevenLabs ("Aria") |
| Zara's voice | OpenAI TTS ("nova") |
| UI | Gradio |

## 3 Takeaways

1. **Personality via prompts** — System prompts give AI agents distinct characters
2. **Voice is ~5 lines of code** — ElevenLabs & OpenAI TTS make it trivial
3. **Agents can talk to each other** — Multi-agent orchestration in action
