"""In-process audio buffer shared between app.py and Temporal activities.

Keeping this in a separate module avoids the double-import problem that a
`from app import _last_audio` inside an Activity would cause: Python would
re-import app.py (since it ran as __main__, not "app") and create a fresh
dict that the main process never sees. This neutral module has no startup
side effects, so both callers receive the same object.
"""

_last_audio: dict[str, bytes | None] = {}
