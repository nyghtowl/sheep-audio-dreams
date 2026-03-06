"""In-process audio buffer shared between app.py and Temporal activities.

Keeping this in a separate module avoids the double-import problem: when
temporal_workflow.py activities do `from app import _last_audio`, Python
re-imports app.py (since it ran as __main__, not "app"), creating a fresh
dict that the main process never sees. Importing from this neutral module
is safe because nothing here triggers app startup side-effects.
"""

_last_audio: dict[str, bytes | None] = {}
