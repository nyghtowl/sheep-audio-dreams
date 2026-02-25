"""Demo script for Temporal presentation.

Run in 3 terminals:

  Terminal 1 — Temporal dev server + Web UI:
    temporal server start-dev

  Terminal 2 — Worker (executes activities):
    .venv/bin/python demo_temporal.py worker

  Terminal 3 — Trigger one game turn:
    .venv/bin/python demo_temporal.py
"""

import asyncio
import logging
import sys
from datetime import timedelta

from dotenv import load_dotenv
load_dotenv()

from temporalio.client import Client
from temporalio.worker import Worker

from temporal_workflow import (
    GameTurnWorkflow,
    generate_dialogue_activity,
    synthesize_voice_activity,
)
from config import AGENTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TASK_QUEUE = "dnd-turns"


async def run_worker():
    """Start a worker that picks up and executes activities."""
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GameTurnWorkflow],
        activities=[generate_dialogue_activity, synthesize_voice_activity],
    )
    logger.info("Worker running on task queue '%s' — check http://localhost:8233", TASK_QUEUE)
    await worker.run()


async def run_turn(agent_name: str = None, turn: int = 0):
    """Execute one game turn via Temporal and print the result."""
    agent_name = agent_name or AGENTS[turn % len(AGENTS)].name
    history = []

    client = await Client.connect("localhost:7233")

    logger.info("Starting workflow for %s...", agent_name)
    agent_name_out, dialogue, audio_bytes = await client.execute_workflow(
        GameTurnWorkflow.run,
        args=[agent_name, history],
        id=f"dnd-turn-{turn}",
        task_queue=TASK_QUEUE,
        execution_timeout=timedelta(minutes=2),
    )

    print(f"\n{'='*50}")
    print(f"Character : {agent_name_out}")
    print(f"Dialogue  : {dialogue}")
    print(f"Audio     : {len(audio_bytes)} bytes")
    print(f"{'='*50}")
    print("\nOpen http://localhost:8233 to see the workflow execution graph.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        asyncio.run(run_worker())
    else:
        turn = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        asyncio.run(run_turn(turn=turn))
