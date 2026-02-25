"""Temporal workflow + activities for D&D turn generation.

Demonstrates: Activities with automatic retries.
If Claude or TTS fails transiently, Temporal retries automatically — no extra code needed.
"""

import base64
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


# ---------------------------------------------------------------------------
# Activities — wrap the existing functions
# ---------------------------------------------------------------------------

@activity.defn
async def generate_dialogue_activity(agent_name: str, history: list[dict]) -> str:
    """Generate dialogue for a character. Auto-retried by Temporal on failure."""
    from config import AGENTS
    from agents import generate_dialogue
    agent = next(a for a in AGENTS if a.name == agent_name)
    return generate_dialogue(agent, history)


@activity.defn
async def synthesize_voice_activity(text: str, agent_name: str) -> bytes:
    """Synthesize voice for text. Auto-retried by Temporal on failure."""
    from config import AGENTS
    from agents import synthesize_voice
    agent = next(a for a in AGENTS if a.name == agent_name)
    return synthesize_voice(text, agent)


# ---------------------------------------------------------------------------
# Workflow — orchestrates activities with retry policy
# ---------------------------------------------------------------------------

RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=500),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


@workflow.defn
class GameTurnWorkflow:
    """One complete D&D turn: generate dialogue → speak it aloud.

    If either API call fails, Temporal retries automatically.
    Visible in the Web UI at http://localhost:8233.
    """

    @workflow.run
    async def run(self, agent_name: str, history: list[dict]) -> tuple[str, str, bytes]:
        dialogue = await workflow.execute_activity(
            generate_dialogue_activity,
            args=[agent_name, history],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICY,
        )

        audio_bytes = await workflow.execute_activity(
            synthesize_voice_activity,
            args=[dialogue, agent_name],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RETRY_POLICY,
        )

        return agent_name, dialogue, audio_bytes


# ---------------------------------------------------------------------------
# Native speech activity + workflow (audio-in → audio-out models)
# ---------------------------------------------------------------------------

@activity.defn
async def generate_turn_audio_activity(
    agent_name: str,
    history: list[dict],
    last_audio_b64: str | None,
) -> dict:
    """Single activity: generate dialogue + audio via native speech model.

    Returns {"dialogue": str, "audio_b64": str}.
    """
    from config import AGENTS
    from agents import generate_turn_audio
    agent = next(a for a in AGENTS if a.name == agent_name)
    last_audio = base64.b64decode(last_audio_b64) if last_audio_b64 else None
    text, audio_bytes = generate_turn_audio(agent, history, last_audio)
    return {
        "dialogue": text,
        "audio_b64": base64.b64encode(audio_bytes).decode(),
    }


@workflow.defn
class NativeSpeechGameTurnWorkflow:
    """One D&D turn using native speech models (dialogue + audio in a single activity call)."""

    @workflow.run
    async def run(
        self,
        agent_name: str,
        history: list[dict],
        last_audio_b64: str | None,
    ) -> dict:
        return await workflow.execute_activity(
            generate_turn_audio_activity,
            args=[agent_name, history, last_audio_b64],
            start_to_close_timeout=timedelta(seconds=45),
            retry_policy=RETRY_POLICY,
        )
