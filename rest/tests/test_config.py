"""Tests for agent configuration."""

from config import AGENTS, DialogueProvider


def test_two_agents_defined():
    """There should be exactly two agents defined."""
    assert len(AGENTS) == 2


def test_lyra_config():
    """Lyra should use the OpenAI audio provider with the correct model and voice."""
    lyra = next(a for a in AGENTS if a.name == "Lyra")
    assert lyra.dialogue_provider == DialogueProvider.OPENAI_AUDIO
    assert lyra.dialogue_model == "gpt-4o-audio-preview"
    assert lyra.native_voice  # must have a voice name


def test_zara_config():
    """Zara should use the Gemini audio provider with an OpenAI TTS fallback voice."""
    zara = next(a for a in AGENTS if a.name == "Zara")
    assert zara.dialogue_provider == DialogueProvider.GEMINI_AUDIO
    assert zara.voice_id  # OpenAI TTS voice fallback


def test_all_agents_have_required_fields():
    """Every agent must have the minimum required fields populated."""
    for agent in AGENTS:
        assert agent.name
        assert agent.role
        assert agent.system_prompt
        assert agent.color.startswith("#")
