"""Gradio UI for the D&D Voice Agents demo."""

import logging
import os
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Must load env vars BEFORE importing agents (which checks for API keys)

import gradio as gr

from agents import GameSession
from config import AGENTS, DM_NARRATION, DialogueProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"

# ---------------------------------------------------------------------------
# Custom CSS — dark fantasy / parchment theme
# ---------------------------------------------------------------------------

CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=MedievalSharp&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap');

:root {
    --parchment: #1a1a2e;
    --parchment-light: #16213e;
    --gold: #e2b44d;
    --text: #e0d6c8;
    --lyra-color: #4a9e6d;
    --zara-color: #9b59b6;
}

.gradio-container {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%) !important;
    font-family: 'Crimson Text', serif !important;
    color: var(--text) !important;
    max-width: 900px !important;
    margin: auto !important;
}

.title-text {
    font-family: 'MedievalSharp', cursive !important;
    text-align: center;
    color: var(--gold) !important;
    font-size: 2.4em !important;
    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
    margin-bottom: 0 !important;
    padding-bottom: 0 !important;
}

.subtitle-text {
    text-align: center;
    color: var(--text) !important;
    font-style: italic;
    opacity: 0.8;
    font-size: 1.1em;
    margin-top: 0 !important;
}

.char-card {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(226,180,77,0.3) !important;
    border-radius: 12px !important;
    padding: 16px !important;
    text-align: center;
}

.char-card h3 {
    font-family: 'MedievalSharp', cursive !important;
    color: var(--gold) !important;
    margin-bottom: 4px !important;
}

.adventure-log {
    border: 2px solid rgba(226,180,77,0.2) !important;
    border-radius: 12px !important;
    background: rgba(0,0,0,0.3) !important;
}

.adventure-log .message {
    font-family: 'Crimson Text', serif !important;
    font-size: 1.05em !important;
}

.dice-display {
    text-align: center;
    font-family: 'MedievalSharp', cursive !important;
    font-size: 1.4em;
    color: var(--gold) !important;
    min-height: 40px;
    padding: 8px;
}

.control-btn {
    font-family: 'MedievalSharp', cursive !important;
    font-size: 1.2em !important;
    padding: 12px 24px !important;
    border-radius: 8px !important;
}

.start-btn {
    background: linear-gradient(135deg, #e2b44d, #c9952e) !important;
    color: #1a1a2e !important;
    border: none !important;
}

.next-btn {
    background: linear-gradient(135deg, #4a9e6d, #3a7e5d) !important;
    color: white !important;
    border: none !important;
}

.tts-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75em;
    font-family: monospace;
    margin-top: 4px;
}

.tts-elevenlabs { background: rgba(74,158,109,0.3); color: #4a9e6d; }
.tts-openai { background: rgba(155,89,182,0.3); color: #9b59b6; }

@keyframes dice-roll {
    0%   { transform: scale(1)   rotate(0deg); }
    20%  { transform: scale(1.5) rotate(-20deg); }
    45%  { transform: scale(1.8) rotate(18deg); }
    65%  { transform: scale(1.5) rotate(-10deg); }
    82%  { transform: scale(1.2) rotate(6deg); }
    100% { transform: scale(1)   rotate(0deg); }
}
.dice-anim {
    display: inline-block;
    animation: dice-roll 0.65s cubic-bezier(0.36, 0.07, 0.19, 0.97);
}

.reset-btn {
    background: linear-gradient(135deg, #c0392b, #922b21) !important;
    color: white !important;
    border: none !important;
}

footer { display: none !important; }
"""

# ---------------------------------------------------------------------------
# Helper to save audio bytes to a temp file for Gradio
# ---------------------------------------------------------------------------

_LATEST_AUDIO_PATHS: dict[str, str | None] = {"wav": None, "mp3": None}


def audio_bytes_to_path(audio_bytes: bytes) -> str:
    """Write audio bytes to a temp file and return the path.

    Detects WAV vs MP3 by header and reuses a separate temp file for each format.
    """
    global _LATEST_AUDIO_PATHS
    suffix = ".wav" if audio_bytes[:4] == b"RIFF" else ".mp3"
    key = suffix.lstrip(".")
    if _LATEST_AUDIO_PATHS[key] is None:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        _LATEST_AUDIO_PATHS[key] = path
    Path(_LATEST_AUDIO_PATHS[key]).write_bytes(audio_bytes)
    return _LATEST_AUDIO_PATHS[key]


# ---------------------------------------------------------------------------
# Character card HTML
# ---------------------------------------------------------------------------


def make_char_card(agent) -> str:
    portrait = ASSETS / f"{agent.name.lower()}.png"
    img_html = ""
    if portrait.exists():
        img_html = f'<img src="/file={portrait}" style="width:100px;height:100px;border-radius:50%;border:2px solid {agent.color};margin-bottom:8px;" /><br/>'

    if agent.dialogue_provider == DialogueProvider.OPENAI_AUDIO:
        tts_class = "tts-openai"
        tts_label = "🎙 OpenAI Audio"
    elif agent.dialogue_provider == DialogueProvider.GEMINI_AUDIO:
        tts_class = "tts-elevenlabs"
        tts_label = "🎙 Gemini Audio"
    else:
        tts_class = "tts-elevenlabs" if agent.tts_provider.value == "elevenlabs" else "tts-openai"
        tts_label = f"🔊 {agent.tts_provider.value.title()}"

    return f"""
    <div style="text-align:center;">
        {img_html}
        <h3 style="color:{agent.color}; font-family:'MedievalSharp',cursive; margin:0;">{agent.name}</h3>
        <p style="color:#e0d6c8; margin:2px 0; font-size:0.95em;">{agent.role}</p>
        <span class="tts-badge {tts_class}">{tts_label}</span>
    </div>
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DICE_IDLE = "<div class='dice-display'>🎲 Awaiting the first roll...</div>"


def _dice_html(dice: str) -> str:
    """Wrap a dice result string in the animated display div."""
    if dice:
        return f"<div class='dice-display'><span class='dice-anim'>{dice}</span></div>"
    return _DICE_IDLE


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def start_adventure(session: GameSession, chat_history: list):
    """Handle the Start Adventure button click."""
    if session.started:
        return session, chat_history, None, "", gr.update(interactive=False), gr.update(interactive=True)

    narration = session.get_opening()
    chat_history.append({"role": "assistant", "content": f"🎭 **Dungeon Master**\n\n{narration}"})

    return session, chat_history, None, "", gr.update(interactive=False), gr.update(interactive=True)


def next_turn(session: GameSession, chat_history: list):
    """Handle the Next Turn button click."""
    if not session.started:
        return session, chat_history, None, ""

    try:
        name, dialogue, audio_bytes, dice = session.next_turn()
    except Exception as e:
        logger.exception("Error during turn generation")
        err_str = str(e).strip() or type(e).__name__
        if "429" in err_str and ("quota" in err_str.lower() or "insufficient_quota" in err_str.lower()):
            hint = (
                "**OpenAI quota exceeded.** Add payment method or check usage at "
                "[platform.openai.com/account/billing](https://platform.openai.com/account/billing)."
            )
        elif "401" in err_str and ("unusual_activity" in err_str.lower() or "free tier" in err_str.lower() or "paid plan" in err_str.lower()):
            hint = (
                "**ElevenLabs** has restricted your account (e.g. Free Tier disabled, or VPN/proxy detected). "
                "Try from a normal connection, or add a [paid subscription](https://elevenlabs.io/subscription) at elevenlabs.io."
            )
        elif "401" in err_str or "invalid" in err_str.lower() or "authentication" in err_str.lower():
            hint = "Check your `.env`: `OPENAI_API_KEY` and `ELEVENLABS_API_KEY` must be set and valid."
        else:
            hint = "Check your `.env` and API keys, or try again in a moment."
        chat_history.append({
            "role": "assistant",
            "content": f"⚠️ *The magical weave flickers... (API error)*\n\n{hint}\n\n<details><summary>Details</summary>\n`{err_str}`\n</details>",
        })
        return session, chat_history, None, ""

    # Find the agent config for color (fallback to first agent if name unknown)
    agent = next((a for a in AGENTS if a.name == name), AGENTS[0])

    # Format the message with character color
    msg = f"**<span style='color:{agent.color}'>{name}</span>** ({agent.role})\n\n{dialogue}"
    chat_history.append({"role": "assistant", "content": msg})

    audio_path = audio_bytes_to_path(audio_bytes)

    return session, chat_history, audio_path, _dice_html(dice)


AUTO_TURNS = 12
AUTO_DELAY = 5.0  # seconds between turns (rough estimate of audio playback time)


def auto_run(session: GameSession, chat_history: list):
    """Generator: run turns automatically with a fixed pause between each."""
    if not session.started:
        return
    for _ in range(AUTO_TURNS):
        try:
            name, dialogue, audio_bytes, dice = session.next_turn()
        except Exception as e:
            logger.exception("Auto-run error")
            chat_history.append({
                "role": "assistant",
                "content": f"⚠️ *Auto-run stopped due to an error.*\n\n`{e}`",
            })
            yield session, chat_history, None, ""
            return
        agent = next((a for a in AGENTS if a.name == name), AGENTS[0])
        msg = f"**<span style='color:{agent.color}'>{name}</span>** ({agent.role})\n\n{dialogue}"
        chat_history.append({"role": "assistant", "content": msg})
        audio_path = audio_bytes_to_path(audio_bytes)
        yield session, chat_history, audio_path, _dice_html(dice)
        time.sleep(AUTO_DELAY)


def reset_game():
    """Reset the session and UI back to the start screen."""
    return (
        GameSession(),
        [],
        None,
        _DICE_IDLE,
        gr.update(interactive=True),
        gr.update(interactive=False),
        gr.update(interactive=False),
    )


# ---------------------------------------------------------------------------
# Build the Gradio app
# ---------------------------------------------------------------------------


def build_app() -> gr.Blocks:
    with gr.Blocks(title="⚔️ The Wild Sheep Chase") as app:
        # Session state
        session_state = gr.State(GameSession())

        # -- Header --
        gr.HTML("<h1 class='title-text'>⚔️ The Wild Sheep Chase ⚔️</h1>")
        gr.HTML(
            "<p class='subtitle-text'>"
            "Two AI adventurers. One enchanted sheep. Zero humans rolling dice."
            "</p>"
        )

        # -- Character cards --
        with gr.Row(equal_height=True):
            for agent in AGENTS:
                with gr.Column():
                    gr.HTML(f"<div class='char-card'>{make_char_card(agent)}</div>")

        # -- Adventure log --
        chatbot = gr.Chatbot(
            label="📜 Adventure Log",
            height=400,
            elem_classes=["adventure-log"],
            avatar_images=None,
        )

        # -- Dice display --
        dice_display = gr.HTML("<div class='dice-display'>🎲 Awaiting the first roll...</div>")

        # -- Audio player --
        audio_player = gr.Audio(
            label="🔊 Latest Voice",
            type="filepath",
            autoplay=True,
            visible=True,
        )

        # -- Control buttons --
        with gr.Row():
            start_btn = gr.Button(
                "🏰 Start Adventure",
                elem_classes=["control-btn", "start-btn"],
                scale=1,
            )
            next_btn = gr.Button(
                "🎲 Next Turn",
                elem_classes=["control-btn", "next-btn"],
                interactive=False,
                scale=1,
            )
            auto_btn = gr.Button(
                "▶ Auto Run",
                elem_classes=["control-btn", "next-btn"],
                interactive=False,
                scale=1,
            )
            reset_btn = gr.Button(
                "🔄 Start Over",
                elem_classes=["control-btn", "reset-btn"],
                scale=1,
            )

        # -- Wiring --
        start_btn.click(
            fn=start_adventure,
            inputs=[session_state, chatbot],
            outputs=[session_state, chatbot, audio_player, dice_display, start_btn, next_btn],
        ).then(
            fn=lambda: gr.update(interactive=True),
            outputs=[auto_btn],
        )

        next_btn.click(
            fn=next_turn,
            inputs=[session_state, chatbot],
            outputs=[session_state, chatbot, audio_player, dice_display],
        )

        auto_btn.click(
            fn=auto_run,
            inputs=[session_state, chatbot],
            outputs=[session_state, chatbot, audio_player, dice_display],
        )

        reset_btn.click(
            fn=reset_game,
            inputs=[],
            outputs=[session_state, chatbot, audio_player, dice_display, start_btn, next_btn, auto_btn],
        )

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860, css=CUSTOM_CSS)
