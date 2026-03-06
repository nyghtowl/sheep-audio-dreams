"""Tests for agent configuration."""

from config import AGENTS, DialogueProvider, TTSProvider


def test_two_agents_defined():
    assert len(AGENTS) == 2


def test_lyra_config():
    lyra = next(a for a in AGENTS if a.name == "Lyra")
    assert lyra.dialogue_provider == DialogueProvider.OPENAI_AUDIO
    assert lyra.dialogue_model == "gpt-4o-audio-preview"
    assert lyra.native_voice  # must have a voice name


def test_zara_config():
    zara = next(a for a in AGENTS if a.name == "Zara")
    assert zara.dialogue_provider == DialogueProvider.GEMINI_AUDIO
    assert zara.voice_id  # OpenAI TTS voice fallback


def test_all_agents_have_required_fields():
    for agent in AGENTS:
        assert agent.name
        assert agent.role
        assert agent.system_prompt
        assert agent.color.startswith("#")
