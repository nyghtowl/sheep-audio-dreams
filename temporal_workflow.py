"""Temporal workflow + activities for the D&D Voice Agents demo.

InteractiveGameWorkflow runs as a single durable workflow per game session.
Each "Next Turn" click sends a Temporal Update that executes the character's
activities and returns the result directly to the UI — no polling needed.

The Temporal Web UI at http://localhost:8233 shows one workflow per game with
activities appearing as nodes as each turn is taken.
"""

import base64
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

@activity.defn
async def generate_dialogue_activity(agent_name: str, history: list[dict]) -> str:
    """Generate dialogue text for a character. Auto-retried by Temporal on failure."""
    from config import AGENTS
    from agents import generate_dialogue
    agent = next(a for a in AGENTS if a.name == agent_name)
    return generate_dialogue(agent, history)


@activity.defn
async def synthesize_voice_activity(text: str, agent_name: str) -> str:
    """Synthesize voice for text. Returns base64-encoded audio. Auto-retried by Temporal on failure."""
    from config import AGENTS
    from agents import synthesize_voice
    agent = next(a for a in AGENTS if a.name == agent_name)
    audio_bytes = synthesize_voice(text, agent)
    return base64.b64encode(audio_bytes).decode()


@activity.defn
async def generate_turn_audio_activity(
    agent_name: str,
    history: list[dict],
    last_audio_b64: str | None,
) -> dict:
    """Single activity: generate dialogue + audio via native speech model (Lyra).

    Lyra hears the previous character's actual audio before responding — one API call
    handles both dialogue generation and voice synthesis.

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


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(milliseconds=500),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
)


# ---------------------------------------------------------------------------
# Interactive game workflow — driven turn-by-turn from the Gradio UI
# ---------------------------------------------------------------------------

@workflow.defn
class InteractiveGameWorkflow:
    """One game session driven by Temporal Updates from the UI.

    Starts when the user clicks "Start Adventure" and stays alive until
    "Start Over". Each "Next Turn" click sends a Temporal update that
    executes the character's activities and returns the result directly
    to the caller — no polling needed.

    One workflow = one game in the Temporal Web UI. Activities appear as
    nodes as each turn is taken.

    Lyra's turns: one generate_turn_audio_activity (native audio, dialogue + voice in one call).
    Zara's turns: generate_dialogue_activity then synthesize_voice_activity — two independent
    nodes that each retry on their own if they fail.
    """

    def __init__(self):
        self._agent_configs: list[dict] = []
        self._history: list[dict] = []
        self._last_audio_b64: str | None = None
        self._turn_index: int = 0
        self._finished: bool = False

    @workflow.run
    async def run(self, agent_configs: list[dict]) -> None:
        self._agent_configs = agent_configs
        # Stay alive until the user resets the game
        await workflow.wait_condition(lambda: self._finished)

    @workflow.update
    async def execute_turn(self) -> dict:
        """Execute one character turn. Called by each Next Turn click.

        Returns {"turn": int, "agent": str, "dialogue": str, "audio_b64": str}.
        """
        cfg = self._agent_configs[self._turn_index % len(self._agent_configs)]
        agent_name = cfg["name"]
        provider = cfg["provider"]

        if provider == "openai_audio":
            result = await workflow.execute_activity(
                generate_turn_audio_activity,
                args=[agent_name, self._history, self._last_audio_b64],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RETRY_POLICY,
            )
            dialogue = result["dialogue"]
            self._last_audio_b64 = result["audio_b64"]
        else:
            dialogue = await workflow.execute_activity(
                generate_dialogue_activity,
                args=[agent_name, self._history],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICY,
            )
            self._last_audio_b64 = await workflow.execute_activity(
                synthesize_voice_activity,
                args=[dialogue, agent_name],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RETRY_POLICY,
            )

        self._history.append({
            "role": "user",
            "content": f"[{agent_name}]: {dialogue}",
        })
        self._turn_index += 1

        return {
            "turn": self._turn_index,
            "agent": agent_name,
            "dialogue": dialogue,
            "audio_b64": self._last_audio_b64,
        }

    @workflow.signal
    async def end_game(self) -> None:
        """Signal the workflow to finish — sent when the user resets the game."""
        self._finished = True
