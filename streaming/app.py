"""FastAPI app for the streaming D&D Voice Agents demo.

Serves a single-page HTML frontend and a WebSocket endpoint that:
1. Starts a Temporal StreamingGameWorkflow for the session
2. Sends execute_turn Updates to the workflow on each turn request
3. Forwards streaming audio chunks from the asyncio.Queue to the browser
4. Handles stop/reset by signalling end_game to the workflow

The Temporal worker is embedded in a background thread (same pattern as
rest/app.py) so Temporal's durable execution wraps every character turn.
If the server crashes mid-turn, restart it and the workflow resumes.

Audio queue pattern:
  streaming_turn_activity → asyncio.Queue[session_id] → WebSocket → browser
  (audio delivery is out-of-band from Temporal's JSON serialisation)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# Add streaming/ to path so imports work when run from repo root
sys.path.insert(0, os.path.dirname(__file__))

from config import AGENTS, DM_NARRATION

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

TASK_QUEUE = "dnd-streaming-game"
STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Audio queues — shared between the FastAPI handlers and the Temporal activities
# ---------------------------------------------------------------------------

# key: session_id → Queue of bytes (PCM16 chunks) or None (end-of-turn sentinel)
_audio_queues: dict[str, asyncio.Queue] = {}

# ---------------------------------------------------------------------------
# Embedded Temporal worker
# ---------------------------------------------------------------------------

_temporal_loop = asyncio.new_event_loop()
_temporal_client = None


async def _start_embedded_worker() -> None:
    global _temporal_client
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
        from temporal_workflow import StreamingGameWorkflow, streaming_turn_activity

        _temporal_client = await Client.connect("localhost:7233")
        worker = Worker(
            _temporal_client,
            task_queue=TASK_QUEUE,
            workflows=[StreamingGameWorkflow],
            activities=[streaming_turn_activity],
        )
        asyncio.create_task(worker.run())
        logger.info("Temporal streaming worker embedded — watch http://localhost:8233")
    except Exception as exc:
        logger.warning("Temporal not reachable (%s) — turns run without Temporal", exc)


def _run_temporal_loop() -> None:
    asyncio.set_event_loop(_temporal_loop)
    _temporal_loop.run_until_complete(_start_embedded_worker())
    _temporal_loop.run_forever()


threading.Thread(target=_run_temporal_loop, daemon=True, name="temporal-streaming-worker").start()


def _temporal_run(coro, timeout: float = 120.0):
    """Run an async Temporal coroutine from sync context."""
    return asyncio.run_coroutine_threadsafe(coro, _temporal_loop).result(timeout=timeout)


# ---------------------------------------------------------------------------
# Temporal helpers
# ---------------------------------------------------------------------------

async def _start_workflow(session_id: str) -> str | None:
    if _temporal_client is None:
        return None
    from temporal_workflow import StreamingGameWorkflow
    agent_configs = [{"name": a.name, "provider": a.dialogue_provider.value} for a in AGENTS]
    workflow_id = f"dnd-streaming-{session_id}"
    await _temporal_client.start_workflow(
        StreamingGameWorkflow.run,
        args=[agent_configs, session_id],
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return workflow_id


async def _execute_turn(workflow_id: str) -> dict:
    from temporal_workflow import StreamingGameWorkflow
    handle = _temporal_client.get_workflow_handle(workflow_id)
    return await handle.execute_update(StreamingGameWorkflow.execute_turn)


async def _end_game(workflow_id: str) -> None:
    from temporal_workflow import StreamingGameWorkflow
    handle = _temporal_client.get_workflow_handle(workflow_id)
    await handle.signal(StreamingGameWorkflow.end_game)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="D&D Voice Agents — Streaming Demo")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Each session gets a fresh audio queue
    queue: asyncio.Queue = asyncio.Queue()
    _audio_queues[session_id] = queue

    workflow_id: str | None = None

    # Background task: forward audio chunks from the queue to the browser
    audio_forward_task = asyncio.create_task(
        _forward_audio(websocket, session_id)
    )

    try:
        async for raw in websocket.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "start":
                # Send opening narration, start workflow
                await websocket.send_json({"type": "narration", "text": DM_NARRATION})
                try:
                    workflow_id = _temporal_run(
                        asyncio.ensure_future(_start_workflow(session_id), loop=_temporal_loop),
                        timeout=15,
                    )
                except Exception as exc:
                    logger.warning("Could not start workflow: %s", exc)
                    workflow_id = None
                await websocket.send_json({"type": "ready"})

            elif msg_type == "next_turn":
                if workflow_id and _temporal_client:
                    try:
                        # Reset the queue before each turn in case of retry
                        _audio_queues[session_id] = asyncio.Queue()
                        result = _temporal_run(
                            asyncio.ensure_future(_execute_turn(workflow_id), loop=_temporal_loop),
                            timeout=120,
                        )
                        await websocket.send_json({
                            "type": "turn_done",
                            "agent": result["agent"],
                            "transcript": result["transcript"],
                            "turn": result["turn"],
                        })
                    except Exception as exc:
                        logger.error("Turn failed: %s", exc)
                        await websocket.send_json({"type": "error", "message": str(exc)})
                else:
                    # Fallback: run turn directly without Temporal
                    await _run_turn_direct(websocket, session_id, queue)

            elif msg_type == "stop":
                if workflow_id and _temporal_client:
                    try:
                        _temporal_run(
                            asyncio.ensure_future(_end_game(workflow_id), loop=_temporal_loop),
                            timeout=10,
                        )
                    except Exception:
                        pass
                # Drain the audio queue
                while not queue.empty():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait(None)
                workflow_id = None
                await websocket.send_json({"type": "stopped"})

    except WebSocketDisconnect:
        pass
    finally:
        audio_forward_task.cancel()
        # Clean up: end workflow if still running
        if workflow_id and _temporal_client:
            try:
                _temporal_run(
                    asyncio.ensure_future(_end_game(workflow_id), loop=_temporal_loop),
                    timeout=5,
                )
            except Exception:
                pass
        _audio_queues.pop(session_id, None)


async def _forward_audio(websocket: WebSocket, session_id: str) -> None:
    """Forward audio chunks from the queue to the browser as binary frames."""
    while True:
        queue = _audio_queues.get(session_id)
        if queue is None:
            await asyncio.sleep(0.05)
            continue
        try:
            chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
            if chunk is None:
                # End-of-turn sentinel — notify browser
                await websocket.send_json({"type": "audio_done"})
            else:
                await websocket.send_bytes(chunk)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break


# ---------------------------------------------------------------------------
# Direct fallback (no Temporal)
# ---------------------------------------------------------------------------

_fallback_history: dict[str, list[dict]] = {}
_fallback_turn_index: dict[str, int] = {}


async def _run_turn_direct(websocket: WebSocket, session_id: str, queue: asyncio.Queue) -> None:
    """Run a turn directly without Temporal (used when Temporal server is unavailable)."""
    from agents import streaming_turn

    history = _fallback_history.setdefault(session_id, [])
    turn_index = _fallback_turn_index.get(session_id, 0)
    agent = AGENTS[turn_index % len(AGENTS)]

    try:
        transcript = await streaming_turn(agent, history, None, queue)
        history.append({"role": "user", "content": f"[{agent.name}]: {transcript}"})
        _fallback_turn_index[session_id] = turn_index + 1
        await websocket.send_json({
            "type": "turn_done",
            "agent": agent.name,
            "transcript": transcript,
            "turn": turn_index + 1,
        })
    except Exception as exc:
        logger.error("Direct turn failed: %s", exc)
        queue.put_nowait(None)
        await websocket.send_json({"type": "error", "message": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
