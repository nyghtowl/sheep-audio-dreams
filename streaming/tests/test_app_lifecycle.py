"""Tests for streaming app startup and process-local shared state."""


def test_import_does_not_start_temporal_worker():
    """The Worker starts in FastAPI lifespan, not as an import side effect."""
    import app

    assert getattr(app, "_temporal_worker_task") is None


def test_app_uses_neutral_shared_audio_state():
    """The WebSocket handler and Activity import the same state objects."""
    import app
    from _shared_state import audio_queues, last_audio

    assert getattr(app, "_audio_queues") is audio_queues
    assert getattr(app, "_last_audio") is last_audio
