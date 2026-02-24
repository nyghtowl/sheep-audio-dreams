"""Game logic, dialogue generation, and voice synthesis for D&D Voice Agents."""

import io
import logging
import os
import random
import re
import struct
from pathlib import Path

from config import AGENTS, DM_NARRATION, AgentConfig, TTSProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock mode — auto-enabled when API keys are missing
# ---------------------------------------------------------------------------

def _has_api_keys() -> bool:
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    eleven_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    return bool(openai_key and eleven_key)


MOCK_MODE = not _has_api_keys()

if MOCK_MODE:
    logger.warning("🎭 MOCK MODE — API keys not found. Using scripted dialogue and silent audio.")
else:
    from elevenlabs import ElevenLabs
    from openai import OpenAI

# ---------------------------------------------------------------------------
# Pre-scripted mock dialogue lines per character
# ---------------------------------------------------------------------------

_MOCK_LINES = {
    "Lyra": [
        "That sheep just kicked the door in harder than most barbarians. Whisper's drawn. Let's move.",
        "I'll take the high ground by the rafters. Roll for perception... that's a 17. I count four goons outside.",
        "Cover me. I'm loosing two arrows at the closest thug — Whisper doesn't miss twice.",
        "The sheep is casting something... or trying to. Zara, keep it alive, I'll hold the door.",
        "Noke just rounded the corner. Big hat, bigger ego. This ends now.",
        "Arrow to the knee? Classic. He's down. Who's next?",
        "We need to break the polymorph. Zara, you're the magic expert — I'll buy you time.",
        "Three more coming up the alley. I'll roll for stealth... nat 18. They won't see me.",
    ],
    "Zara": [
        "A TALKING SHEEP?! This is the best day of my life! Ember, are you seeing this?!",
        "I cast Detect Magic — oh WOW the arcane threads on this sheep are incredible! Wild Magic surge? Yes please!",
        "Stand back everyone! I'm channeling a bolt of chromatic energy at the door! ...it's pink. I meant for it to be pink.",
        "Ember, fireball formation! Just kidding — we're indoors. Firebolt it is. FWOOSH!",
        "I'll roll an Arcana check to analyze the polymorph... 19! I can see the weave, I just need time to unravel it!",
        "Noke, you absolute FOOL! You messed with a sheep AND a wild magic sorceress?! CHAOS BOLT!",
        "The surge of magic is beautiful! Slightly on fire, but beautiful! Someone pat out my cloak, please.",
        "I'm weaving the counter-spell now. Hold them off, Lyra! This sheep is about to be a wizard again!",
    ],
}

_mock_turn_counters: dict[str, int] = {}


def _mock_dialogue(config: AgentConfig) -> str:
    lines = _MOCK_LINES.get(config.name, ["*stays in character mysteriously*"])
    idx = _mock_turn_counters.get(config.name, 0)
    line = lines[idx % len(lines)]
    _mock_turn_counters[config.name] = idx + 1
    return line


def _generate_silent_mp3() -> bytes:
    """Generate a minimal valid MP3 frame (~0.3s of silence)."""
    # A minimal MPEG Audio Layer 3 frame: sync word + valid header + zero-padded data
    # Frame header for MPEG1 Layer3 128kbps 44100Hz stereo
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    # MPEG1 Layer3 128kbps frame is 417 bytes (header + data)
    frame = header + b"\x00" * 413
    # Repeat a few frames so players don't choke on a single frame
    return frame * 12


# ---------------------------------------------------------------------------
# API clients (initialized lazily on first use)
# ---------------------------------------------------------------------------

_openai_client = None
_elevenlabs_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()  # reads OPENAI_API_KEY from env
    return _openai_client


def _get_elevenlabs():
    global _elevenlabs_client
    if _elevenlabs_client is None:
        _elevenlabs_client = ElevenLabs()  # reads ELEVENLABS_API_KEY from env
    return _elevenlabs_client


# ---------------------------------------------------------------------------
# Dialogue generation (OpenAI GPT-4o)
# ---------------------------------------------------------------------------


def generate_dialogue(
    config: AgentConfig,
    history: list[dict[str, str]],
) -> str:
    """Generate a character's next line of dialogue using OpenAI."""
    if MOCK_MODE:
        return _mock_dialogue(config)

    messages = [
        {"role": "system", "content": config.system_prompt},
        *history,
    ]
    response = _get_openai().chat.completions.create(
        model="gpt-4o",
        messages=messages,
        max_tokens=150,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Voice synthesis (ElevenLabs or OpenAI TTS)
# ---------------------------------------------------------------------------


def _strip_stage_directions(text: str) -> str:
    """Remove action/narration markers so TTS only speaks dialogue."""
    # Remove *action text* and _action text_
    cleaned = re.sub(r'\*[^*]+\*', '', text)
    cleaned = re.sub(r'(?<![\w])_[^_]+_(?![\w])', '', cleaned)
    # Remove (parenthetical asides) and [bracketed actions]
    cleaned = re.sub(r'\([^)]*\)', '', cleaned)
    cleaned = re.sub(r'\[[^\]]*\]', '', cleaned)
    # Remove emoji
    cleaned = re.sub(r'[\U0001F300-\U0001FAFF\U00002702-\U000027B0]', '', cleaned)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or text  # fallback to original if everything was stripped


def synthesize_voice(text: str, config: AgentConfig) -> bytes:
    """Convert text to speech using the agent's configured TTS provider."""
    speech_text = _strip_stage_directions(text)
    logger.info("TTS input for %s: %s", config.name, speech_text)

    if MOCK_MODE:
        return _generate_silent_mp3()

    if config.tts_provider == TTSProvider.ELEVENLABS:
        return _synthesize_elevenlabs(speech_text, config.voice_id)
    return _synthesize_openai(speech_text, config.voice_id)


def _synthesize_elevenlabs(text: str, voice_id: str) -> bytes:
    """Generate speech via ElevenLabs."""
    client = _get_elevenlabs()
    audio_iter = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )
    # The SDK returns an iterator of bytes chunks
    return b"".join(audio_iter)


def _synthesize_openai(text: str, voice: str) -> bytes:
    """Generate speech via OpenAI TTS."""
    client = _get_openai()
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )
    return response.content


# ---------------------------------------------------------------------------
# Dice rolling
# ---------------------------------------------------------------------------


def roll_d20() -> int:
    """Roll a d20 and return the result."""
    return random.randint(1, 20)


def format_roll(result: int) -> str:
    """Format a dice roll for display."""
    if result == 20:
        return f"🎲 **NAT 20!** 🎉"
    if result == 1:
        return f"🎲 **Critical fail...** (1)"
    return f"🎲 Rolled a **{result}**"


# ---------------------------------------------------------------------------
# Game session
# ---------------------------------------------------------------------------


class GameSession:
    """Manages the state of a D&D conversation between agents."""

    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self.turn_index: int = 0
        self.agents = AGENTS
        self.started: bool = False
        self.last_roll: str = ""

    def get_opening(self) -> str:
        """Return the DM's opening narration."""
        self.started = True
        return DM_NARRATION

    def next_turn(self) -> tuple[str, str, bytes, str]:
        """Execute the next character's turn.

        Returns:
            (character_name, dialogue_text, audio_bytes, dice_display)
        """
        agent = self.agents[self.turn_index % len(self.agents)]
        logger.info("Generating dialogue for %s", agent.name)

        # Generate dialogue
        dialogue = generate_dialogue(agent, self.history)

        # Check for dice roll triggers — append result so the character speaks it
        dice_display = ""
        roll_keywords = ["roll for", "roll a", "check", "saving throw", "nat "]
        if any(kw in dialogue.lower() for kw in roll_keywords):
            result = roll_d20()
            dice_display = format_roll(result)
            if result == 20:
                dialogue += " That's a nat 20!"
            elif result == 1:
                dialogue += " ...a one. Critical fail."
            else:
                dialogue += f" That's a {result}."

        # Synthesize voice (includes the spoken roll result)
        logger.info("Synthesizing voice for %s via %s", agent.name, agent.tts_provider.value)
        audio_bytes = synthesize_voice(dialogue, agent)

        # Update conversation history — the other agent sees this as a "user" message
        self.history.append({
            "role": "user",
            "content": f"[{agent.name}]: {dialogue}",
        })

        self.turn_index += 1
        self.last_roll = dice_display

        return agent.name, dialogue, audio_bytes, dice_display
