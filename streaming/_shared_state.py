"""Process-local audio state shared by the streaming app and activities.

Audio intentionally stays outside Temporal history. The FastAPI app and its
embedded Worker run on the same asyncio event loop, so they can hand PCM chunks
through these queues without crossing threads.
"""

import asyncio


# Session ID -> queue of PCM16 chunks, with None marking the end of a turn.
audio_queues: dict[str, asyncio.Queue[bytes | None]] = {}

# Session ID -> the previous character's PCM16 output, retained for one turn.
last_audio: dict[str, bytes | None] = {}
