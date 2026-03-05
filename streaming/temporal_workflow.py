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
import base64
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
    last_audio_b64: str | None,
    session_id: str,
) -> dict:
    """Stream one character turn and return the transcript.

    Audio chunks are pushed into an in-process asyncio.Queue keyed by
    session_id. The FastAPI WebSocket handler reads from that queue and
    forwards bytes to the browser in real time.

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

    # _audio_queues lives in app.py (same process as the worker)
    # Import lazily so this module doesn't require app.py at import time
    try:
        from app import _audio_queues
    except ImportError:
        # Fallback for testing: use a local queue
        _audio_queues = {}

    agent = next(a for a in AGENTS if a.name == agent_name)
    last_audio = base64.b64decode(last_audio_b64) if last_audio_b64 else None

    # On retry, reset the queue so stale chunks aren't replayed
    queue: asyncio.Queue = asyncio.Queue()
    _audio_queues[session_id] = queue

    transcript = await streaming_turn(agent, history, last_audio, queue)

    # Collect audio for passing to the next character (used as last_audio_b64)
    # The queue was consumed by the WebSocket handler during the activity, so
    # we capture audio separately via a collecting wrapper if needed. For now
    # we return the transcript only — audio context passes via text history.
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
            args=[agent_name, self._history, None, self._session_id],
            start_to_close_timeout=timedelta(seconds=120),
            heartbeat_timeout=timedelta(seconds=10),
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

    @workflow.signal
    async def end_game(self) -> None:
        """Signal the workflow to finish — sent when the user stops the game."""
        self._finished = True
