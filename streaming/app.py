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

# Previous turn's raw PCM bytes — read by each activity as audio input for the
# next character, written after each turn completes. Lives only in this process;
# never serialized through Temporal. On crash+restart the next turn starts with
# text-only context, which is fine.
_last_audio: dict[str, bytes | None] = {}

# Turn index for Temporal-managed sessions (mirrors workflow._turn_index locally
# so we can send turn_start before the blocking execute_turn call)
_temporal_turn_index: dict[str, int] = {}

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


async def _get_or_start_workflow(session_id: str) -> str | None:
    """Return existing running workflow ID for this session, or start a fresh one.

    On reconnect after a server crash the browser sends 'start' again with the
    same session_id (persisted in sessionStorage). If Temporal still has a running
    workflow for that session, we rejoin it — no new workflow is started.
    """
    if _temporal_client is None:
        return None
    workflow_id = f"dnd-streaming-{session_id}"
    try:
        handle = _temporal_client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status.name == "RUNNING":
            logger.info("Rejoining existing workflow %s", workflow_id)
            return workflow_id
    except Exception:
        pass
    return await _start_workflow(session_id)


async def _get_workflow_turn_index(workflow_id: str) -> int:
    """Query the workflow for its current turn index to sync the server's local counter."""
    from temporal_workflow import StreamingGameWorkflow
    handle = _temporal_client.get_workflow_handle(workflow_id)
    return await handle.query(StreamingGameWorkflow.get_turn_index)


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
                # Send opening narration, start or rejoin workflow
                await websocket.send_json({"type": "narration", "text": DM_NARRATION})
                try:
                    loop = asyncio.get_event_loop()
                    workflow_id = await loop.run_in_executor(
                        None, lambda: _temporal_run(_get_or_start_workflow(session_id), timeout=15)
                    )
                    # On rejoin, sync local turn counter from the live workflow
                    if workflow_id and _temporal_client:
                        try:
                            turn_idx = await loop.run_in_executor(
                                None, lambda: _temporal_run(_get_workflow_turn_index(workflow_id), timeout=5)
                            )
                            _temporal_turn_index[session_id] = turn_idx
                        except Exception:
                            pass  # non-fatal — turn_start indicator may be off by one
                except Exception as exc:
                    logger.warning("Could not start/rejoin workflow: %s", exc)
                    workflow_id = None
                await websocket.send_json({"type": "ready"})

            elif msg_type == "next_turn":
                if workflow_id and _temporal_client:
                    try:
                        # Reset the queue before each turn in case of retry
                        _audio_queues[session_id] = asyncio.Queue()
                        # Notify the browser which character is about to speak
                        # (before blocking on execute_turn so the indicator fires immediately)
                        turn_index = _temporal_turn_index.get(session_id, 0)
                        agent = AGENTS[turn_index % len(AGENTS)]
                        await websocket.send_json({
                            "type": "turn_start",
                            "agent": agent.name,
                            "turn": turn_index + 1,
                        })
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(
                            None, lambda: _temporal_run(_execute_turn(workflow_id), timeout=120)
                        )
                        _temporal_turn_index[session_id] = turn_index + 1
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
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, lambda: _temporal_run(_end_game(workflow_id), timeout=10)
                        )
                    except Exception:
                        pass
                # Drain the current active queue (may differ from initial `queue`
                # after next_turn replaced _audio_queues[session_id] on retry)
                active_queue = _audio_queues.get(session_id)
                if active_queue:
                    while not active_queue.empty():
                        try:
                            active_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    active_queue.put_nowait(None)
                workflow_id = None
                _temporal_turn_index.pop(session_id, None)
                _last_audio.pop(session_id, None)
                await websocket.send_json({"type": "stopped"})

    except WebSocketDisconnect:
        pass
    finally:
        audio_forward_task.cancel()
        # Clean up: end workflow if still running
        if workflow_id and _temporal_client:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None, lambda: _temporal_run(_end_game(workflow_id), timeout=5)
                )
            except Exception:
                pass
        _audio_queues.pop(session_id, None)
        _temporal_turn_index.pop(session_id, None)
        _last_audio.pop(session_id, None)


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
        await websocket.send_json({"type": "turn_start", "agent": agent.name, "turn": turn_index + 1})
        audio_out: list[bytes] = []
        transcript = await streaming_turn(agent, history, _last_audio.get(session_id), queue, audio_out=audio_out)
        history.append({"role": "user", "content": f"[{agent.name}]: {transcript}"})
        audio_bytes = b"".join(audio_out)
        _last_audio[session_id] = audio_bytes if audio_bytes else None
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
