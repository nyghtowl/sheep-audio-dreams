"""Streaming agent functions for the D&D Voice Agents demo.

Each character connects to a streaming speech API over WebSocket and pushes
PCM16 audio chunks into an asyncio.Queue as they arrive. The FastAPI WebSocket
handler reads from the queue and forwards chunks to the browser in real time,
so the first audio reaches the user within <1s of starting the turn.

Lyra  → OpenAI Realtime API (gpt-4o-realtime-preview)
Zara  → Gemini Live (gemini-2.5-flash-native-audio)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct

from temporalio import activity

from config import AgentConfig, DialogueProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _resample_24k_to_16k(audio: bytes) -> bytes:
    """Downsample PCM16 from 24 kHz mono to 16 kHz using linear interpolation.

    Lyra (OpenAI Realtime) outputs PCM16 at 24 kHz; Gemini Live expects 16 kHz
    input. The 3:2 ratio means every 3 input samples produce 2 output samples.
    """
    if not audio:
        return audio
    if len(audio) % 2:
        audio = audio[:-1]
    samples = struct.unpack(f"<{len(audio) // 2}h", audio)
    n_in = len(samples)
    n_out = int(n_in * 2 // 3)
    out: list[int] = []
    for i in range(n_out):
        pos = i * 1.5  # step 1.5 input samples per output sample
        j = int(pos)
        frac = pos - j
        if j + 1 < n_in:
            val = int(samples[j] * (1.0 - frac) + samples[j + 1] * frac)
        else:
            val = samples[min(j, n_in - 1)]
        out.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{len(out)}h", *out)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openai_client():
    import openai
    return openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _gemini_client():
    from google import genai
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
    audio_out: list[bytes] | None = None,
) -> str:
    """Stream Lyra's turn via gpt-4o-realtime-preview.

    Sends conversation context + the previous character's audio (if any),
    then receives audio delta events and pushes PCM16 chunks to the queue.
    If audio_out is provided, chunks are also appended there for the caller
    to pass as input to the next character's turn.
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
                if audio_out is not None:
                    audio_out.append(chunk)
                try:
                    activity.heartbeat()
                except Exception:
                    pass  # not in activity context (direct execution without Temporal)
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
    audio_out: list[bytes] | None = None,
) -> str:
    """Stream Zara's turn via Gemini Live (gemini-2.5-flash-native-audio).

    When last_audio is provided (Lyra's PCM16 at 24 kHz), resamples it to
    16 kHz and sends it via send_realtime_input with manual activity control
    (auto-VAD disabled so the model waits for activity_end before responding).
    Conversation history is baked into the system instruction so we can use
    send_realtime_input exclusively without interleaving send_client_content.

    When no prior audio is available (first Zara turn), falls back to
    send_client_content with text-only context.

    Puts None into the queue when the turn is complete.
    Returns the transcript text.
    """
    from google.genai import types

    client = _gemini_client()
    transcript_parts: list[str] = []
    text_context = _build_text_context(history)
    has_audio = bool(last_audio)

    # When using send_realtime_input for audio we cannot interleave
    # send_client_content in the same session. Bake the conversation history
    # into the system instruction so Zara has full context without a separate
    # text turn.
    if has_audio:
        full_system = f"{agent.system_prompt}\n\nConversation so far:\n{text_context}"
    else:
        full_system = agent.system_prompt

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=agent.voice_id)
            )
        ),
        system_instruction=full_system,
        # Request a text transcript of Zara's audio output so it can be
        # displayed in the adventure log alongside Lyra's transcript.
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # Disable automatic VAD when replaying pre-recorded audio so the model
        # waits for our explicit activity_end signal rather than responding as
        # soon as it detects silence mid-stream.
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        ) if has_audio else None,
    )

    # Safety cap: ~25s of PCM16 at 24 kHz (25s × 24000 samples/s × 2 bytes = 1.2 MB)
    MAX_AUDIO_BYTES = 1_200_000

    async with client.aio.live.connect(model=agent.dialogue_model, config=config) as session:
        if has_audio:
            # Resample Lyra's 24 kHz PCM16 to 16 kHz — Gemini Live expects
            # 16 kHz input audio (audio/pcm;rate=16000).
            audio_16k = _resample_24k_to_16k(last_audio)
            logger.info("Zara: sending %d bytes of 16 kHz audio via send_realtime_input", len(audio_16k))
            await session.send_realtime_input(activity_start=types.ActivityStart())
            # Send in ~1 s chunks (32 KB at 16 kHz PCM16)
            chunk_size = 32_000
            for i in range(0, len(audio_16k), chunk_size):
                await session.send_realtime_input(
                    audio=types.Blob(
                        data=audio_16k[i : i + chunk_size],
                        mime_type="audio/pcm;rate=16000",
                    )
                )
            await session.send_realtime_input(activity_end=types.ActivityEnd())
            logger.info("Zara: activity_end sent — waiting for response")
        else:
            # No prior audio (first Zara turn): send text-only context.
            await session.send_client_content(
                turns=types.Content(
                    parts=[types.Part(text=text_context)],
                    role="user",
                ),
                turn_complete=True,
            )

        total_audio_bytes = 0
        response_count = 0
        try:
            async for response in session.receive():
                response_count += 1
                if response.data:
                    total_audio_bytes += len(response.data)
                    if total_audio_bytes > MAX_AUDIO_BYTES:
                        logger.warning("Zara audio exceeded byte limit — ending turn early")
                        break
                    await queue.put(response.data)  # raw PCM bytes
                    if audio_out is not None:
                        audio_out.append(response.data)
                    try:
                        activity.heartbeat()
                    except Exception:
                        pass  # not in activity context (direct execution without Temporal)
                if (
                    response.server_content
                    and response.server_content.output_transcription
                    and response.server_content.output_transcription.text
                ):
                    transcript_parts.append(response.server_content.output_transcription.text)
                if response.server_content and response.server_content.turn_complete:
                    logger.info(
                        "Zara: turn_complete received (responses=%d, audio=%d bytes)",
                        response_count, total_audio_bytes,
                    )
                    break
        except Exception as e:
            logger.error("Zara stream error after %d responses: %s", response_count, e)

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
    audio_out: list[bytes] | None = None,
) -> str:
    """Run one character turn, streaming audio chunks into queue.

    Routes to the appropriate streaming API based on agent.dialogue_provider.
    Puts None into the queue when the turn is complete (stop signal for the
    WebSocket handler).

    If audio_out is provided (a list), audio chunks are also appended to it
    so the caller can pass the raw PCM bytes as input to the next character.

    Returns the transcript text for appending to conversation history.
    """
    if agent.dialogue_provider == DialogueProvider.OPENAI_REALTIME:
        return await _lyra_streaming_turn(agent, history, last_audio, queue, audio_out)
    if agent.dialogue_provider == DialogueProvider.GEMINI_LIVE:
        return await _zara_streaming_turn(agent, history, last_audio, queue, audio_out)
    raise ValueError(f"Unsupported dialogue provider: {agent.dialogue_provider}")
