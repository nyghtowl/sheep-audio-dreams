"""Temporal workflow + activities for the D&D Voice Agents demo.

InteractiveGameWorkflow runs as a single durable workflow per game session.
Each "Next Turn" click sends a Temporal Update that executes the character's
activities and returns the result directly to the UI — no polling needed.

The Temporal Web UI at http://localhost:8233 shows one workflow per game with
activities appearing as nodes as each turn is taken.
"""

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
async def synthesize_voice_activity(text: str, agent_name: str, session_id: str) -> None:
    """Synthesize voice for text. Auto-retried by Temporal on failure.

    Stores raw audio bytes in app._last_audio[session_id] so the UI and the
    next character can access the audio. Audio never passes through Temporal
    serialisation — only text state transits Temporal.
    """
    from config import AGENTS
    from agents import synthesize_voice
    from _shared_state import _last_audio
    agent = next(a for a in AGENTS if a.name == agent_name)
    audio_bytes = synthesize_voice(text, agent)
    _last_audio[session_id] = audio_bytes


@activity.defn
async def generate_dm_reaction_activity(name: str, dialogue: str, roll: int) -> str:
    """Ask the DM model to narrate the roll outcome. Auto-retried by Temporal on failure."""
    from agents import generate_dm_reaction
    return generate_dm_reaction(name, dialogue, roll)


@activity.defn
async def generate_turn_audio_activity(
    agent_name: str,
    history: list[dict],
    session_id: str,
) -> dict:
    """Single activity: generate dialogue + audio via native speech model (Lyra).

    Reads the previous character's audio from app._last_audio[session_id] (in-process
    memory) so it never passes through Temporal serialisation. Writes this turn's
    audio back to _last_audio so the UI and the next character can access it.

    Returns {"dialogue": str} — audio is in _last_audio, not in the Temporal payload.
    """
    from config import AGENTS
    from agents import generate_turn_audio
    from _shared_state import _last_audio
    agent = next(a for a in AGENTS if a.name == agent_name)
    last_audio = _last_audio.get(session_id)
    text, audio_bytes = generate_turn_audio(agent, history, last_audio)
    _last_audio[session_id] = audio_bytes
    return {"dialogue": text}


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
        self._session_id: str = ""
        self._turn_index: int = 0
        self._finished: bool = False

    @workflow.run
    async def run(self, agent_configs: list[dict]) -> None:
        """Start the workflow, store agent configs, and wait for end_game signal."""
        self._agent_configs = agent_configs
        # Use the workflow ID as the session key for in-memory audio lookup
        self._session_id = workflow.info().workflow_id
        # Stay alive until the user resets the game
        await workflow.wait_condition(lambda: self._finished)

    @workflow.update
    async def execute_turn(self) -> dict:
        """Execute one character turn. Called by each Next Turn click.

        Audio is stored in app._last_audio[session_id] by the activities and
        read directly by _get_turn — it never transits Temporal serialisation.

        Returns {"turn": int, "agent": str, "dialogue": str, "dm_text": str, "roll": int}.
        """
        cfg = self._agent_configs[self._turn_index % len(self._agent_configs)]
        agent_name = cfg["name"]

        roll = workflow.random().randint(1, 20)

        result = await workflow.execute_activity(
            generate_turn_audio_activity,
            args=[agent_name, self._history, self._session_id],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RETRY_POLICY,
        )
        dialogue = result["dialogue"]

        dm_text = await workflow.execute_activity(
            generate_dm_reaction_activity,
            args=[agent_name, dialogue, roll],
            start_to_close_timeout=timedelta(seconds=8),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=200),
                maximum_attempts=2,
            ),
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
            "dm_text": dm_text,
            "roll": roll,
        }

    @workflow.signal
    async def end_game(self) -> None:
        """Signal the workflow to finish — sent when the user resets the game."""
        self._finished = True
