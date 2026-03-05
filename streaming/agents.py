"""Streaming agent functions for the D&D Voice Agents demo.

Each character connects to a streaming speech API over WebSocket and pushes
PCM16 audio chunks into an asyncio.Queue as they arrive. The FastAPI WebSocket
handler reads from the queue and forwards chunks to the browser in real time,
so the first audio reaches the user within <1s of starting the turn.

Lyra  → OpenAI Realtime API (gpt-4o-realtime-preview)
Zara  → Gemini Live (gemini-2.0-flash-live-001)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

from temporalio import activity

from config import AgentConfig, DialogueProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openai_client():
    import openai
    return openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _gemini_client():
    import google.genai as genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _build_text_context(history: list[dict]) -> str:
    """Flatten conversation history into a single prompt string."""
    if not history:
        return "The adventure is just beginning. Start the scene."
    lines = []
    for msg in history[-10:]:  # last 10 turns to stay within context limits
        lines.append(msg["content"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lyra — OpenAI Realtime API
# ---------------------------------------------------------------------------

async def _lyra_streaming_turn(
    agent: AgentConfig,
    history: list[dict],
    last_audio: bytes | None,
    queue: asyncio.Queue,
) -> str:
    """Stream Lyra's turn via gpt-4o-realtime-preview.

    Sends conversation context + the previous character's audio (if any),
    then receives audio delta events and pushes PCM16 chunks to the queue.
    Puts None into the queue when the turn is complete.

    Returns the transcript text.
    """
    client = _openai_client()
    transcript = ""

    async with client.beta.realtime.connect(model=agent.dialogue_model) as conn:
        # Configure the session
        await conn.session.update(session={
            "modalities": ["audio", "text"],
            "instructions": agent.system_prompt,
            "voice": agent.voice_id,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": None,  # manual turn management
        })

        # Build user message content: text context + optional prior audio
        content = []
        text_context = _build_text_context(history)
        content.append({"type": "input_text", "text": text_context})

        if last_audio:
            content.append({
                "type": "input_audio",
                "audio": base64.b64encode(last_audio).decode(),
            })

        await conn.conversation.item.create(item={
            "type": "message",
            "role": "user",
            "content": content,
        })
        await conn.response.create()

        async for event in conn:
            if event.type == "response.audio.delta":
                chunk = base64.b64decode(event.delta)
                await queue.put(chunk)
                activity.heartbeat()
            elif event.type == "response.audio_transcript.done":
                transcript = event.transcript
            elif event.type == "response.done":
                break
            elif event.type == "error":
                logger.error("OpenAI Realtime error: %s", event.error)
                break

    await queue.put(None)  # sentinel: end of turn
    return transcript


# ---------------------------------------------------------------------------
# Zara — Gemini Live
# ---------------------------------------------------------------------------

async def _zara_streaming_turn(
    agent: AgentConfig,
    history: list[dict],
    last_audio: bytes | None,
    queue: asyncio.Queue,
) -> str:
    """Stream Zara's turn via Gemini Live (gemini-2.0-flash-live-001).

    Sends conversation context + the previous character's audio (if any),
    then receives audio chunks and pushes them to the queue.
    Puts None into the queue when the turn is complete.

    Returns the transcript text.
    """
    import google.genai.types as types

    client = _gemini_client()
    transcript_parts: list[str] = []

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=agent.voice_id)
            )
        ),
        system_instruction=agent.system_prompt,
    )

    async with client.aio.live.connect(model=agent.dialogue_model, config=config) as session:
        # Build the input: text context first, then audio if available
        parts: list[types.Part] = []
        text_context = _build_text_context(history)
        parts.append(types.Part(text=text_context))

        if last_audio:
            parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type="audio/pcm",
                    data=last_audio,
                )
            ))

        await session.send(input=types.Content(parts=parts), end_of_turn=True)

        async for response in session.receive():
            if response.data:
                await queue.put(response.data)  # raw PCM bytes
                activity.heartbeat()
            if response.text:
                transcript_parts.append(response.text)
            if response.server_content and response.server_content.turn_complete:
                break

    await queue.put(None)  # sentinel: end of turn
    return "".join(transcript_parts)


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------

async def streaming_turn(
    agent: AgentConfig,
    history: list[dict],
    last_audio: bytes | None,
    queue: asyncio.Queue,
) -> str:
    """Run one character turn, streaming audio chunks into queue.

    Routes to the appropriate streaming API based on agent.dialogue_provider.
    Puts None into the queue when the turn is complete (stop signal for the
    WebSocket handler).

    Returns the transcript text for appending to conversation history.
    """
    if agent.dialogue_provider == DialogueProvider.OPENAI_REALTIME:
        return await _lyra_streaming_turn(agent, history, last_audio, queue)
    elif agent.dialogue_provider == DialogueProvider.GEMINI_LIVE:
        return await _zara_streaming_turn(agent, history, last_audio, queue)
    else:
        raise ValueError(f"Unsupported dialogue provider: {agent.dialogue_provider}")
