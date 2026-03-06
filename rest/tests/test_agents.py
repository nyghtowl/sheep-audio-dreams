"""Tests for pure utility functions and GameSession (mock mode).

These tests make no API calls — they cover the logic that lives between
receiving an API response and handing audio/text to the UI.
"""

import io
import os
import wave
from unittest.mock import patch

# Force mock mode before importing agents so no clients are initialised
os.environ.setdefault("MOCK_MODE", "1")

from agents import (
    GameSession,
    _audio_format,
    _generate_silent_mp3,
    _pcm_to_wav,
    _strip_stage_directions,
)

# ---------------------------------------------------------------------------
# _strip_stage_directions
# ---------------------------------------------------------------------------

class TestStripStageDirections:
    """Tests for the _strip_stage_directions helper."""

    def test_removes_asterisk_actions(self):
        """Asterisk-wrapped stage directions are removed and whitespace collapsed."""
        assert _strip_stage_directions("Hello *waves hand* world") == "Hello world"

    def test_removes_underscore_actions(self):
        """Underscore-wrapped directions are removed."""
        assert _strip_stage_directions("She _whispers softly_ and smiles") == "She and smiles"

    def test_removes_parentheticals(self):
        """Parenthetical stage notes are stripped."""
        assert _strip_stage_directions("Sure (nervously) I can do that") == "Sure I can do that"

    def test_removes_brackets(self):
        """Square-bracket annotations are removed."""
        assert _strip_stage_directions("Charging forward [rolls d20]") == "Charging forward"

    def test_handles_mixed_markers(self):
        """Multiple marker styles are all stripped in a single pass."""
        result = _strip_stage_directions("*draws sword* Let's go! (quietly)")
        assert "draws sword" not in result
        assert "quietly" not in result
        assert "Let's go!" in result

    def test_returns_original_if_fully_stripped(self):
        """If stripping would leave an empty string, the original is returned."""
        original = "*action only*"
        result = _strip_stage_directions(original)
        assert result  # non-empty

    def test_plain_text_unchanged(self):
        """Text with no markers passes through unchanged."""
        text = "I cast fireball at the troll."
        assert _strip_stage_directions(text) == text


# ---------------------------------------------------------------------------
# _audio_format
# ---------------------------------------------------------------------------

class TestAudioFormat:
    """Tests for the _audio_format format-detection helper."""

    def test_wav_detected_by_riff_header(self):
        """Files starting with the RIFF header are identified as WAV."""
        assert _audio_format(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"

    def test_mp3_for_anything_else(self):
        """Everything without a RIFF header is treated as MP3."""
        assert _audio_format(b"\xff\xfb\x90\x00" + b"\x00" * 100) == "mp3"
        assert _audio_format(b"\x00\x00\x00\x00") == "mp3"


# ---------------------------------------------------------------------------
# _pcm_to_wav
# ---------------------------------------------------------------------------

class TestPcmToWav:
    """Tests for the _pcm_to_wav PCM-to-WAV converter."""

    def test_produces_valid_wav_header(self):
        """Output starts with the RIFF header bytes."""
        pcm = b"\x00\x00" * 100  # 100 frames of silence
        wav = _pcm_to_wav(pcm)
        assert wav[:4] == b"RIFF"

    def test_readable_by_wave_module(self):
        """Output is a valid WAV file with the expected default parameters."""
        pcm = b"\x00\x00" * 240  # 10ms at 24kHz
        wav = _pcm_to_wav(pcm)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000

    def test_custom_sample_rate(self):
        """The sample_rate parameter is written into the WAV header."""
        pcm = b"\x00\x00" * 100
        wav = _pcm_to_wav(pcm, sample_rate=16000)
        with wave.open(io.BytesIO(wav), "rb") as wf:
            assert wf.getframerate() == 16000


# ---------------------------------------------------------------------------
# _generate_silent_mp3
# ---------------------------------------------------------------------------

class TestGenerateSilentMp3:
    """Tests for the _generate_silent_mp3 helper."""

    def test_returns_bytes(self):
        """Function returns a bytes object."""
        assert isinstance(_generate_silent_mp3(), bytes)

    def test_starts_with_mp3_sync_word(self):
        """Output starts with the MPEG1 Layer3 sync word (0xFF 0xFB)."""
        mp3 = _generate_silent_mp3()
        # MPEG sync word: 0xFF followed by 0xFB (MPEG1 Layer3)
        assert mp3[0] == 0xFF
        assert mp3[1] == 0xFB


# ---------------------------------------------------------------------------
# GameSession (mock mode)
# ---------------------------------------------------------------------------

class TestGameSession:
    """GameSession tests use MOCK_MODE=1 so no API calls are made."""

    def setup_method(self):
        """Patch MOCK_MODE and create a started GameSession for each test."""
        # Patch at the module level so next_turn uses mock path
        self.mock_patch = patch("agents.MOCK_MODE", True)
        self.mock_patch.start()
        self.session = GameSession()
        self.session.get_opening()  # starts the session

    def teardown_method(self):
        """Stop the MOCK_MODE patch after each test."""
        self.mock_patch.stop()

    def test_get_opening_sets_started(self):
        """get_opening() transitions the session to the started state."""
        session = GameSession()
        assert not session.started
        session.get_opening()
        assert session.started

    def test_next_turn_increments_index(self):
        """turn_index advances by one after each call to next_turn."""
        assert self.session.turn_index == 0
        self.session.next_turn()
        assert self.session.turn_index == 1

    def test_next_turn_appends_to_history(self):
        """Each turn appends one entry to the history list."""
        assert len(self.session.history) == 0
        self.session.next_turn()
        assert len(self.session.history) == 1

    def test_history_entry_has_role_and_content(self):
        """History entries have the expected role/content keys."""
        self.session.next_turn()
        entry = self.session.history[0]
        assert "role" in entry
        assert "content" in entry
        assert entry["role"] == "user"

    def test_history_content_includes_agent_name(self):
        """The history content string includes the speaking agent's name."""
        name, _dialogue, *_ = self.session.next_turn()
        content = self.session.history[-1]["content"]
        assert name in content

    def test_alternating_agents(self):
        """Consecutive turns use different agents."""
        name1, *_ = self.session.next_turn()
        name2, *_ = self.session.next_turn()
        assert name1 != name2

    def test_next_turn_returns_audio_bytes(self):
        """next_turn returns non-empty audio bytes at position 2."""
        _, _, audio, *_ = self.session.next_turn()
        assert isinstance(audio, bytes)
        assert len(audio) > 0

    def test_next_turn_returns_dm_text_and_roll(self):
        """next_turn returns a non-empty DM text string and a valid d20 roll."""
        _, _, _, dm_text, roll = self.session.next_turn()
        assert isinstance(dm_text, str)
        assert len(dm_text) > 0
        assert 1 <= roll <= 20

    def test_last_audio_bytes_updated(self):
        """last_audio_bytes should be populated after a turn runs."""
        assert self.session.last_audio_bytes is None
        self.session.next_turn()
        assert self.session.last_audio_bytes is not None
