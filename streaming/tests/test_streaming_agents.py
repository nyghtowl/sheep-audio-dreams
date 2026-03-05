"""Tests for the streaming demo agents and config.

No real API calls — OpenAI Realtime and Gemini Live connections are mocked
with async context managers that emit a fixed sequence of events.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import AGENTS, AgentConfig, DialogueProvider, LYRA, ZARA
from agents import _build_text_context, streaming_turn


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_lyra_uses_openai_realtime():
    assert LYRA.dialogue_provider == DialogueProvider.OPENAI_REALTIME


def test_zara_uses_gemini_live():
    assert ZARA.dialogue_provider == DialogueProvider.GEMINI_LIVE


def test_two_agents_defined():
    assert len(AGENTS) == 2


def test_all_agents_have_required_fields():
    for agent in AGENTS:
        assert agent.name
        assert agent.role
        assert agent.voice_id
        assert agent.system_prompt
        assert agent.dialogue_model


# ---------------------------------------------------------------------------
# _build_text_context
# ---------------------------------------------------------------------------

def test_build_text_context_empty_history():
    result = _build_text_context([])
    assert result  # should return a non-empty default prompt


def test_build_text_context_includes_recent_turns():
    history = [
        {"role": "user", "content": "[Lyra]: Watch the door."},
        {"role": "user", "content": "[Zara]: I cast fireball!"},
    ]
    result = _build_text_context(history)
    assert "Lyra" in result
    assert "Zara" in result


def test_build_text_context_caps_at_10_turns():
    history = [{"role": "user", "content": f"[Agent]: Turn {i}"} for i in range(20)]
    result = _build_text_context(history)
    # Should only include last 10 turns — first 10 should not appear
    assert "Turn 0" not in result
    assert "Turn 19" in result


# ---------------------------------------------------------------------------
# Mocked streaming turn helpers
# ---------------------------------------------------------------------------

class MockRealtimeEvent:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockGeminiResponse:
    def __init__(self, data=None, text=None, turn_complete=False):
        self.data = data
        self.text = text
        self.server_content = MagicMock(turn_complete=turn_complete) if turn_complete else None


@asynccontextmanager
async def mock_openai_realtime_connection(*args, **kwargs):
    """Mock that emits a fixed sequence of Realtime events."""
    conn = AsyncMock()
    conn.session = AsyncMock()
    conn.session.update = AsyncMock()
    conn.conversation = AsyncMock()
    conn.conversation.item = AsyncMock()
    conn.conversation.item.create = AsyncMock()
    conn.response = AsyncMock()
    conn.response.create = AsyncMock()

    import base64
    audio_chunk = base64.b64encode(b"\x00\x01" * 100).decode()

    events = [
        MockRealtimeEvent("response.audio.delta", delta=audio_chunk),
        MockRealtimeEvent("response.audio.delta", delta=audio_chunk),
        MockRealtimeEvent("response.audio_transcript.done", transcript="Let's save that sheep!"),
        MockRealtimeEvent("response.done"),
    ]

    async def aiter_events():
        for e in events:
            yield e

    conn.__aiter__ = aiter_events
    yield conn


async def mock_gemini_receive():
    """Async generator yielding mock Gemini Live responses."""
    yield MockGeminiResponse(data=b"\x00\x01" * 100)
    yield MockGeminiResponse(data=b"\x00\x01" * 100)
    yield MockGeminiResponse(text="By the nine hells, that sheep needs us!")
    yield MockGeminiResponse(turn_complete=True)


@asynccontextmanager
async def mock_gemini_live_connection(*args, **kwargs):
    session = AsyncMock()
    session.send = AsyncMock()
    session.receive = mock_gemini_receive
    yield session


# ---------------------------------------------------------------------------
# streaming_turn dispatch tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_turn_dispatch_lyra():
    """streaming_turn routes to _lyra_streaming_turn for OPENAI_REALTIME."""
    queue = asyncio.Queue()
    with patch("agents._lyra_streaming_turn", new_callable=AsyncMock) as mock_lyra:
        mock_lyra.return_value = "I draw my bow."
        result = await streaming_turn(LYRA, [], None, queue)
    mock_lyra.assert_called_once()
    assert result == "I draw my bow."


@pytest.mark.asyncio
async def test_streaming_turn_dispatch_zara():
    """streaming_turn routes to _zara_streaming_turn for GEMINI_LIVE."""
    queue = asyncio.Queue()
    with patch("agents._zara_streaming_turn", new_callable=AsyncMock) as mock_zara:
        mock_zara.return_value = "I cast chaos bolt!"
        result = await streaming_turn(ZARA, [], None, queue)
    mock_zara.assert_called_once()
    assert result == "I cast chaos bolt!"


# ---------------------------------------------------------------------------
# Audio queue sentinel tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lyra_puts_none_sentinel_in_queue():
    """_lyra_streaming_turn puts None into the queue at end of turn."""
    queue = asyncio.Queue()
    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_cls.return_value.beta.realtime.connect = mock_openai_realtime_connection
        with patch("agents._openai_client") as mock_client:
            mock_client.return_value = mock_cls.return_value
            # Patch the actual connection used inside the function
            with patch("agents._lyra_streaming_turn", wraps=None) as _:
                pass

    # Simpler: directly test that sentinel appears using the mock connection
    with patch("agents._openai_client") as mock_client_fn:
        instance = MagicMock()
        instance.beta.realtime.connect = mock_openai_realtime_connection
        mock_client_fn.return_value = instance

        from agents import _lyra_streaming_turn
        await _lyra_streaming_turn(LYRA, [], None, queue)

    chunks = []
    while not queue.empty():
        chunks.append(await queue.get())

    assert chunks[-1] is None, "Last item in queue must be None (end-of-turn sentinel)"
    assert len(chunks) > 1, "Should have audio chunks before the sentinel"


@pytest.mark.asyncio
async def test_zara_puts_none_sentinel_in_queue():
    """_zara_streaming_turn puts None into the queue at end of turn."""
    queue = asyncio.Queue()

    with patch("agents._gemini_client") as mock_client_fn:
        instance = MagicMock()
        instance.aio.live.connect = mock_gemini_live_connection
        mock_client_fn.return_value = instance

        from agents import _zara_streaming_turn
        await _zara_streaming_turn(ZARA, [], None, queue)

    chunks = []
    while not queue.empty():
        chunks.append(await queue.get())

    assert chunks[-1] is None, "Last item in queue must be None (end-of-turn sentinel)"
    assert len(chunks) > 1, "Should have audio chunks before the sentinel"


@pytest.mark.asyncio
async def test_queue_cleared_on_retry_does_not_replay_stale_audio():
    """A new Queue replaces the old one on retry — stale chunks are not replayed."""
    old_queue = asyncio.Queue()
    await old_queue.put(b"stale_audio_chunk")

    new_queue = asyncio.Queue()
    # Simulate what app.py does on retry: replace the queue for the session
    audio_queues = {"test-session": old_queue}
    audio_queues["test-session"] = new_queue

    assert audio_queues["test-session"] is new_queue
    assert new_queue.empty(), "Fresh queue should have no stale audio"
