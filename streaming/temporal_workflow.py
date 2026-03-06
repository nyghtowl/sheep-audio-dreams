"""Temporal workflow + activities for the streaming D&D Voice Agents demo.

StreamingGameWorkflow runs as a single durable workflow per game session.
Each turn sends a Temporal Update that executes streaming_turn_activity and
returns the transcript directly to the caller.

The activity opens a WebSocket connection to the AI API, streams audio chunks
into an asyncio.Queue (shared with the FastAPI WebSocket handler), and returns
the transcript when the turn completes. If the connection drops mid-turn,
Temporal retries the activity from the start of that turn.

Key difference from the REST demo:
- REST: activity returns full audio bytes → Gradio plays the file
- Streaming: activity streams chunks into a queue → browser receives them live
"""

import asyncio
import logging
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

logger = logging.getLogger(__name__)

MAX_TURNS = 12

RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=500),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

@activity.defn
async def streaming_turn_activity(
    agent_name: str,
    history: list[dict],
    session_id: str,
) -> dict:
    """Stream one character turn and return the transcript.

    Audio chunks are pushed into an in-process asyncio.Queue keyed by
    session_id. The FastAPI WebSocket handler reads from that queue and
    forwards bytes to the browser in real time.

    Previous-turn audio is read from and written to app._last_audio (in-process
    memory) so it never passes through Temporal serialisation. Temporal only
    tracks text state: turn index, transcript history, session lifecycle.

    Heartbeats on every audio chunk so Temporal knows the activity is alive
    during the streaming connection (which can run 10-30s per turn).

    Returns {"transcript": str, "agent": str}.
    """
    # Import here (inside activity) to avoid workflow sandbox issues
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))

    from config import AGENTS
    from agents import streaming_turn

    # Both _audio_queues and _last_audio live in app.py (same process).
    # Import lazily so this module doesn't require app.py at import time.
    try:
        from app import _audio_queues, _last_audio
    except ImportError:
        # Fallback for testing: use local dicts
        _audio_queues = {}
        _last_audio = {}

    agent = next(a for a in AGENTS if a.name == agent_name)

    # Read previous turn's audio from in-memory store (never serialized by Temporal)
    last_audio = _last_audio.get(session_id)

    # On retry, reset the queue so stale chunks aren't replayed
    queue: asyncio.Queue = asyncio.Queue()
    _audio_queues[session_id] = queue

    # Collect audio while streaming — audio_out captures a copy of every chunk
    # so we can store it for the next character without re-reading the queue.
    audio_out: list[bytes] = []
    transcript = await streaming_turn(agent, history, last_audio, queue, audio_out=audio_out)

    # Store this turn's audio for the next character (in-memory, not in Temporal).
    # Cap at ~2s of PCM16 at 24kHz — Gemini Live rejects large inline audio blobs.
    # Align to 2-byte boundary required by PCM16.
    MAX_PASS_AUDIO = 96_000  # 2s × 24000 Hz × 2 bytes/sample
    audio_bytes = b"".join(audio_out)[:MAX_PASS_AUDIO]
    if len(audio_bytes) % 2:
        audio_bytes = audio_bytes[:-1]
    _last_audio[session_id] = audio_bytes if audio_bytes else None

    return {"transcript": transcript, "agent": agent_name}


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@workflow.defn
class StreamingGameWorkflow:
    """One game session driven by Temporal Updates from the FastAPI server.

    Stays alive until end_game signal or MAX_TURNS is reached. Each Next Turn
    request sends a Temporal Update that runs streaming_turn_activity and
    returns the transcript directly — no polling needed.

    Audio delivery is out-of-band (asyncio.Queue → WebSocket → browser).
    Temporal tracks state: turn index, conversation history, session lifecycle.
    """

    def __init__(self):
        self._agent_configs: list[dict] = []
        self._history: list[dict] = []
        self._turn_index: int = 0
        self._finished: bool = False
        self._session_id: str = ""

    @workflow.run
    async def run(self, agent_configs: list[dict], session_id: str) -> None:
        """Start the workflow and wait until stopped or MAX_TURNS reached."""
        self._agent_configs = agent_configs
        self._session_id = session_id
        await workflow.wait_condition(
            lambda: self._finished or self._turn_index >= MAX_TURNS
        )

    @workflow.update
    async def execute_turn(self) -> dict:
        """Execute one character turn and return the transcript.

        Called by each Next Turn request from the FastAPI server.
        Returns {"turn": int, "agent": str, "transcript": str}.
        """
        cfg = self._agent_configs[self._turn_index % len(self._agent_configs)]
        agent_name = cfg["name"]

        result = await workflow.execute_activity(
            streaming_turn_activity,
            args=[agent_name, self._history, self._session_id],
            start_to_close_timeout=timedelta(seconds=120),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICY,
        )

        transcript = result["transcript"]
        self._history.append({
            "role": "user",
            "content": f"[{agent_name}]: {transcript}",
        })
        self._turn_index += 1

        return {
            "turn": self._turn_index,
            "agent": agent_name,
            "transcript": transcript,
        }

    @workflow.query
    def get_turn_index(self) -> int:
        """Return the current turn index — used by the server to sync state on reconnect."""
        return self._turn_index

    @workflow.signal
    async def end_game(self) -> None:
        """Signal the workflow to finish — sent when the user stops the game."""
        self._finished = True
