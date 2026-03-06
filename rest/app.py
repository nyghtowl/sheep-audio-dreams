"""Gradio UI for the D&D Voice Agents demo.

The app embeds a Temporal worker so every turn runs as a durable activity.
When the Temporal server is running (temporal server start-dev), the full
execution graph is visible at http://localhost:8233 as you play.

If the Temporal server is not running, the app falls back to calling the
agents directly — the UI still works, just without Temporal.
"""

import asyncio
import concurrent.futures
import io
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # Must load env vars BEFORE importing agents (which checks for API keys)

import gradio as gr

from agents import GameSession
from config import AGENTS, DM_NARRATION, DialogueProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
TASK_QUEUE = "dnd-turns"

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


.reset-btn {
    background: linear-gradient(135deg, #c0392b, #922b21) !important;
    color: white !important;
    border: none !important;
}

footer { display: none !important; }
"""

# ---------------------------------------------------------------------------
# Embedded Temporal worker
# ---------------------------------------------------------------------------

from temporalio.client import Client
from temporalio.worker import Worker
from temporal_workflow import (
    InteractiveGameWorkflow,
    generate_dialogue_activity,
    generate_dm_reaction_activity,
    generate_turn_audio_activity,
    synthesize_voice_activity,
)

_temporal_loop = asyncio.new_event_loop()
_temporal_client: Client | None = None


async def _start_embedded_worker() -> None:
    """Connect to Temporal and start the worker as a background asyncio task."""
    global _temporal_client
    try:
        _temporal_client = await Client.connect("localhost:7233")
        worker = Worker(
            _temporal_client,
            task_queue=TASK_QUEUE,
            workflows=[InteractiveGameWorkflow],
            activities=[
                generate_turn_audio_activity,
                generate_dialogue_activity,
                synthesize_voice_activity,
                generate_dm_reaction_activity,
            ],
        )
        asyncio.create_task(worker.run())
        logger.info("Temporal worker embedded — watch http://localhost:8233")
    except Exception as exc:
        logger.warning("Temporal not reachable (%s) — turns run without Temporal", exc)


def _run_temporal_loop() -> None:
    asyncio.set_event_loop(_temporal_loop)
    _temporal_loop.run_until_complete(_start_embedded_worker())
    _temporal_loop.run_forever()


threading.Thread(target=_run_temporal_loop, daemon=True, name="temporal-worker").start()


def _temporal_run(coro, timeout: float = 90.0):
    """Submit a coroutine to the Temporal background loop and block until done."""
    return asyncio.run_coroutine_threadsafe(coro, _temporal_loop).result(timeout=timeout)


# ---------------------------------------------------------------------------
# Helper to save audio bytes to a temp file for Gradio
# ---------------------------------------------------------------------------

# Previous turn's raw audio bytes — shared with Temporal activities via
# _shared_state so both sides reference the same dict object. Lives only in
# this process; never serialized through Temporal.
from _shared_state import _last_audio

_LATEST_AUDIO_PATHS: dict[str, str | None] = {"wav": None, "mp3": None}


def _audio_duration_seconds(audio_bytes: bytes) -> float:
    """Return audio duration in seconds (WAV exact, MP3 estimated at 128kbps)."""
    if audio_bytes[:4] == b"RIFF":
        try:
            import wave as _wave
            buf = io.BytesIO(audio_bytes)
            with _wave.open(buf, "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            pass
    return len(audio_bytes) * 8 / 128_000


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


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def start_adventure(session: GameSession, chat_history: list, workflow_id: str | None):
    """Handle the Start Adventure button: show opening narration and start the workflow."""
    if session.started:
        return (
            session, chat_history, None,
            gr.update(interactive=False), gr.update(interactive=True),
            gr.update(interactive=True), workflow_id,
        )

    narration = session.get_opening()
    chat_history.append({"role": "assistant", "content": f"🎭 **Dungeon Master**\n\n{narration}"})

    # Start a Temporal workflow for this game session
    new_workflow_id = None
    if _temporal_client is not None:
        agent_configs = [
            {"name": a.name, "provider": a.dialogue_provider.value} for a in AGENTS
        ]
        new_workflow_id = f"dnd-game-{uuid.uuid4().hex[:8]}"
        try:
            _temporal_run(
                _temporal_client.start_workflow(
                    InteractiveGameWorkflow.run,
                    args=[agent_configs],
                    id=new_workflow_id,
                    task_queue=TASK_QUEUE,
                    execution_timeout=timedelta(hours=2),
                )
            )
            logger.info("Started InteractiveGameWorkflow %s", new_workflow_id)
        except Exception as exc:
            logger.warning("Could not start Temporal workflow: %s — using direct mode", exc)
            new_workflow_id = None

    return (
        session, chat_history, None,
        gr.update(interactive=False), gr.update(interactive=True),
        gr.update(interactive=True), new_workflow_id,
    )


def _get_turn(session: GameSession, workflow_id: str | None) -> tuple[str, str, bytes, str, int]:
    """Execute one turn via Temporal if available, otherwise call agents directly.

    Returns (agent_name, dialogue, audio_bytes, dm_text, roll).
    """
    if workflow_id and _temporal_client is not None:
        handle = _temporal_client.get_workflow_handle(workflow_id)
        result = _temporal_run(handle.execute_update(InteractiveGameWorkflow.execute_turn))
        # Audio lives in _last_audio (written by the activity), never in Temporal payloads
        audio_bytes = _last_audio.get(workflow_id) or b""
        return result["agent"], result["dialogue"], audio_bytes, result["dm_text"], result["roll"]

    # Fallback: call agents directly (Temporal not available)
    return session.next_turn()


def next_turn(session: GameSession, chat_history: list, workflow_id: str | None, pending_dm: dict | None):
    """Handle the Next Turn button click.

    Shows the previous turn's DM reaction first (pending_dm), then runs the
    current character's turn. DM text is stored in state and shown at the top
    of the *next* click so it appears after the audio has played.
    """
    if not session.started:
        return session, chat_history, None, None

    # Show the previous turn's DM reaction before running this turn
    if pending_dm:
        chat_history.append({
            "role": "assistant",
            "content": f"🎲 *{pending_dm['roll']} — {pending_dm['dm_text']}*",
        })

    try:
        name, dialogue, audio_bytes, dm_text, roll = _get_turn(session, workflow_id)
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
        return session, chat_history, None, None

    agent = next((a for a in AGENTS if a.name == name), AGENTS[0])
    msg = (
        f"**<span style='color:{agent.color}'>{name}</span>** ({agent.role})\n\n"
        f"{dialogue}"
    )
    chat_history.append({"role": "assistant", "content": msg})
    audio_path = audio_bytes_to_path(audio_bytes)
    return session, chat_history, audio_path, {"dm_text": dm_text, "roll": roll}


AUTO_TURNS = 12
AUTO_BUFFER = 1.5  # seconds of silence to add after audio finishes before next turn


def auto_run(session: GameSession, chat_history: list, workflow_id: str | None, pending_dm: dict | None):
    """Generator: prefetch next turn in background while current audio plays."""
    if not session.started:
        return

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_get_turn, session, workflow_id)

    for i in range(AUTO_TURNS):
        try:
            name, dialogue, audio_bytes, dm_text, roll = future.result()
        except Exception as e:
            logger.exception("Auto-run error")
            chat_history.append({
                "role": "assistant",
                "content": f"⚠️ *Auto-run stopped due to an error.*\n\n`{e}`",
            })
            yield session, chat_history, None, None
            executor.shutdown(wait=False)
            return

        # Prefetch the next turn while this audio plays
        if i < AUTO_TURNS - 1:
            future = executor.submit(_get_turn, session, workflow_id)

        # Show previous turn's DM reaction before this character speaks
        if pending_dm:
            chat_history.append({
                "role": "assistant",
                "content": f"🎲 *{pending_dm['roll']} — {pending_dm['dm_text']}*",
            })

        agent = next((a for a in AGENTS if a.name == name), AGENTS[0])
        msg = (
            f"**<span style='color:{agent.color}'>{name}</span>** ({agent.role})\n\n"
            f"{dialogue}"
        )
        chat_history.append({"role": "assistant", "content": msg})
        audio_path = audio_bytes_to_path(audio_bytes)
        duration = _audio_duration_seconds(audio_bytes)
        pending_dm = {"dm_text": dm_text, "roll": roll}
        yield session, chat_history, audio_path, pending_dm
        time.sleep(duration + AUTO_BUFFER)

    executor.shutdown(wait=False)


def reset_game(workflow_id: str | None):
    """Reset the session and UI. Ends the Temporal workflow if one is running."""
    if workflow_id and _temporal_client is not None:
        try:
            _temporal_run(
                _temporal_client.get_workflow_handle(workflow_id)
                    .signal(InteractiveGameWorkflow.end_game)
            )
            logger.info("Sent end_game signal to workflow %s", workflow_id)
        except Exception:
            pass  # workflow may have already ended or timed out
    _last_audio.pop(workflow_id, None)

    return (
        GameSession(),
        [],
        None,
        gr.update(interactive=True),
        gr.update(interactive=False),
        gr.update(interactive=False),
        None,  # clear workflow_id
        None,  # clear pending_dm
    )


# ---------------------------------------------------------------------------
# Build the Gradio app
# ---------------------------------------------------------------------------


def build_app() -> gr.Blocks:
    with gr.Blocks(title="⚔️ The Wild Sheep Chase") as app:
        session_state = gr.State(GameSession())
        workflow_id_state = gr.State(None)
        pending_dm_state = gr.State(None)

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
            inputs=[session_state, chatbot, workflow_id_state],
            outputs=[
                session_state, chatbot, audio_player,
                start_btn, next_btn, auto_btn, workflow_id_state,
            ],
        )

        next_btn.click(
            fn=next_turn,
            inputs=[session_state, chatbot, workflow_id_state, pending_dm_state],
            outputs=[session_state, chatbot, audio_player, pending_dm_state],
        )

        auto_event = auto_btn.click(
            fn=auto_run,
            inputs=[session_state, chatbot, workflow_id_state, pending_dm_state],
            outputs=[session_state, chatbot, audio_player, pending_dm_state],
        )

        reset_btn.click(
            fn=reset_game,
            inputs=[workflow_id_state],
            outputs=[
                session_state, chatbot, audio_player,
                start_btn, next_btn, auto_btn, workflow_id_state, pending_dm_state,
            ],
            cancels=[auto_event],
        )

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7860, css=CUSTOM_CSS)
