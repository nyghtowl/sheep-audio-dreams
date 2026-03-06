"""Game logic, dialogue generation, and voice synthesis for D&D Voice Agents."""

import base64
import io
import logging
import os
import random
import re
import wave
from config import (
    AGENTS, AgentConfig, DialogueProvider, DM_NARRATION, TTSProvider,
    ZARA_ELEVENLABS_VOICE_ID,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock mode — auto-enabled when API keys are missing
# ---------------------------------------------------------------------------

def _use_gtts() -> bool:
    return os.environ.get("USE_GTTS", "").strip().lower() in ("1", "true", "yes")


def _has_api_keys() -> bool:
    """At least one dialogue engine and one TTS provider."""
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    eleven_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    has_dialogue = bool(openai_key or anthropic_key)
    # OpenAI key covers both dialogue and TTS (gpt-4o-audio-preview does both in one call)
    has_tts = bool(eleven_key or _use_gtts() or openai_key)
    return bool(has_dialogue and has_tts)


def _use_claude_for_dialogue() -> bool:
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def _force_mock() -> bool:
    return os.environ.get("MOCK_MODE", "").strip() in ("1", "true", "yes")

MOCK_MODE = not _has_api_keys() or _force_mock()

if MOCK_MODE:
    logger.warning("🎭 MOCK MODE — API keys not found or MOCK_MODE=1. Using scripted dialogue and silent audio.")
else:
    from elevenlabs import ElevenLabs
    from openai import OpenAI  # needed for both GPT dialogue and gpt-4o-audio-preview
    if _use_claude_for_dialogue():
        from anthropic import Anthropic

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
        "Stand back everyone! I'm channeling a bolt of chromatic energy at the door! "
        "...it's pink. I meant for it to be pink.",
        "Ember, fireball formation! Just kidding — we're indoors. Firebolt it is. FWOOSH!",
        "I'll roll an Arcana check to analyze the polymorph... 19! "
        "I can see the weave, I just need time to unravel it!",
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
_anthropic_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()  # reads OPENAI_API_KEY from env
    return _openai_client


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _anthropic_client


def _get_elevenlabs():
    global _elevenlabs_client
    if _elevenlabs_client is None:
        api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        _elevenlabs_client = ElevenLabs(api_key=api_key or None)  # pass explicitly so header is set
    return _elevenlabs_client


# ---------------------------------------------------------------------------
# Dialogue generation (OpenAI GPT-4o or Claude)
# ---------------------------------------------------------------------------


def generate_dialogue(
    config: AgentConfig,
    history: list[dict[str, str]],
) -> str:
    """Generate a character's next line of dialogue using OpenAI or Claude."""
    if MOCK_MODE:
        return _mock_dialogue(config)

    if _use_claude_for_dialogue():
        # Claude requires at least one message; first turn has empty history.
        messages = history if history else [
            {
                "role": "user",
                "content": "The scene is set. You are first to speak. Deliver your opening line in character.",
            },
        ]
        response = _get_anthropic().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            system=config.system_prompt,
            messages=messages,
            temperature=0.9,
        )
        return (response.content[0].text or "").strip()
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


def _use_elevenlabs_for_zara() -> bool:
    """Use ElevenLabs for Zara's TTS — only if a key is actually available."""
    if _use_gtts():
        return False
    if not (os.environ.get("ELEVENLABS_API_KEY") or "").strip():
        return False  # no key, fall through to OpenAI TTS
    if os.environ.get("USE_ELEVENLABS_FOR_ALL_VOICES", "").strip().lower() in ("1", "true", "yes"):
        return True
    return _use_claude_for_dialogue()


def synthesize_voice(text: str, config: AgentConfig) -> bytes:
    """Convert text to speech using the agent's configured TTS provider."""
    speech_text = _strip_stage_directions(text)
    logger.info("TTS input for %s: %s", config.name, speech_text)

    if MOCK_MODE:
        return _generate_silent_mp3()

    if _use_gtts():
        return _synthesize_gtts(speech_text)
    if config.tts_provider == TTSProvider.ELEVENLABS:
        return _synthesize_elevenlabs(speech_text, config.voice_id)
    if config.tts_provider == TTSProvider.OPENAI and _use_elevenlabs_for_zara():
        return _synthesize_elevenlabs(speech_text, ZARA_ELEVENLABS_VOICE_ID)
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


def _synthesize_gtts(text: str) -> bytes:
    """Generate speech via Google TTS (free, no API key required)."""
    from gtts import gTTS
    fp = io.BytesIO()
    gTTS(text=text, lang="en").write_to_fp(fp)
    return fp.getvalue()


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
# Native speech helpers (audio-in → audio-out models)
# ---------------------------------------------------------------------------

_gemini_client = None


def _has_gemini_key() -> bool:
    return bool((os.environ.get("GEMINI_API_KEY") or "").strip())


def _audio_format(audio_bytes: bytes) -> str:
    """Detect audio format from header bytes (WAV vs MP3)."""
    return "wav" if audio_bytes[:4] == b"RIFF" else "mp3"


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw 16-bit mono PCM bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def _generate_lyra_audio(
    config: AgentConfig,
    history: list[dict],
    last_audio: bytes | None,
) -> tuple[str, bytes]:
    """Call gpt-4o-audio-preview. Lyra hears the previous character's voice.

    Returns (transcript, wav_bytes).
    """
    context_text = "\n".join(e["content"] for e in history) if history else ""

    user_content: list[dict] = []
    if context_text:
        user_content.append({
            "type": "text",
            "text": (
                f"Previous exchanges in this adventure:\n{context_text}\n\n"
                "React to the above and continue the story. ONE short sentence only. No stage directions."
            ),
        })
    else:
        user_content.append({
            "type": "text",
            "text": "The scene is set. You are first to speak. Deliver your opening line in character.",
        })

    if last_audio is not None:
        user_content.append({
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(last_audio).decode(),
                "format": _audio_format(last_audio),
            },
        })

    response = _get_openai().chat.completions.create(
        model=config.dialogue_model,
        modalities=["text", "audio"],
        audio={"voice": config.native_voice, "format": "wav"},
        messages=[
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    msg = response.choices[0].message
    wav_bytes = base64.b64decode(msg.audio.data)
    transcript = (msg.audio.transcript or "").strip()
    return transcript, wav_bytes


def _generate_zara_audio(
    config: AgentConfig,
    history: list[dict],
    _last_audio: bytes | None,
) -> tuple[str, bytes]:
    """Gemini two-step: gemini-2.5-flash for dialogue text, gemini-2.5-flash-preview-tts for voice.

    Returns (dialogue_text, wav_bytes).
    """
    from google.genai import types

    context_text = "\n".join(e["content"] for e in history) if history else "The scene is set."

    # Step 1: generate dialogue text — Gemini Flash with Claude fallback
    try:
        prompt = (
            f"Prior context:\n{context_text}\n\n"
            "Continue the dialogue as your character. ONE short sentence only. No stage directions."
        )
        text_response = _get_gemini().models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=config.system_prompt,
                max_output_tokens=200,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        # Extract non-thought text parts explicitly (thinking models split response into parts)
        raw_parts = text_response.candidates[0].content.parts
        text_parts = [
            p.text for p in raw_parts
            if p.text and not getattr(p, "thought", False)
        ]
        transcript = " ".join(text_parts).strip() or (text_response.text or "").strip()
    except Exception as e:
        logger.warning("Gemini text generation failed (%s) — falling back to Claude for Zara", e)
        transcript = generate_dialogue(config, history)

    # Step 2: synthesize Zara's voice with OpenAI TTS
    audio_bytes = _synthesize_openai(transcript, config.voice_id)
    return transcript, audio_bytes


def generate_turn_audio(
    config: AgentConfig,
    history: list[dict],
    last_audio: bytes | None,
) -> tuple[str, bytes]:
    """Generate dialogue + audio for a character's turn.

    Routes to native speech models when configured; falls back to legacy
    text+TTS pipeline otherwise.

    Returns (text_transcript, audio_bytes).
    """
    if MOCK_MODE:
        text = _mock_dialogue(config)
        return text, _generate_silent_mp3()

    provider = config.dialogue_provider

    if provider == DialogueProvider.OPENAI_AUDIO:
        logger.info("Native audio for %s via %s", config.name, config.dialogue_model)
        return _generate_lyra_audio(config, history, last_audio)

    if provider == DialogueProvider.GEMINI_AUDIO:
        if _has_gemini_key():
            logger.info("Native audio for %s via %s", config.name, config.dialogue_model)
            return _generate_zara_audio(config, history, last_audio)
        logger.warning("GEMINI_API_KEY not set — falling back to legacy TTS for %s", config.name)

    # Legacy text + TTS path (CLAUDE_TEXT, OPENAI_TEXT, or Gemini fallback)
    text = generate_dialogue(config, history)
    audio = synthesize_voice(text, config)
    return text, audio


# ---------------------------------------------------------------------------
# Dice rolling & DM narration
# ---------------------------------------------------------------------------

def roll_d20() -> int:
    """Roll a d20 and return the result (1–20)."""
    return random.randint(1, 20)


_DM_SYSTEM = (
    "You are a terse Dungeon Master narrating outcomes in a D&D adventure. "
    "A character just acted and rolled a d20. Write exactly one punchy sentence "
    "describing what happens next. "
    "Roll 15-20: dramatic success or unexpected boon. "
    "Roll 6-14: partial success or mixed outcome. "
    "Roll 1-5: complication, mishap, or failure with a twist. "
    "No quotation marks. No stage directions. Present tense."
)

_MOCK_DM_REACTIONS: dict[str, list[str]] = {
    "high": [
        "The arrow flies true and the thug crumples before he can shout a warning.",
        "The spell detonates perfectly — the door splinters into embers and awe.",
        "Every guard in the tavern freezes, weapons raised but hands trembling.",
        "Fate smiles: the lock clicks open and the way forward is clear.",
    ],
    "mid": [
        "The blow lands, but your opponent staggers back rather than falling.",
        "The magic works, though a nearby torch sputters an ominous warning.",
        "You advance, but the floorboard groans loud enough to be heard upstairs.",
        "The plan holds — for now — though something feels off about the shadows.",
    ],
    "low": [
        "The shot clips a beam overhead; splinters rain down and alert the guards.",
        "A wild surge of magic bounces off the ceiling and singes your own cape.",
        "Your footing slips on a damp stone — the enemy lunges while you recover.",
        "The door opens, but straight into a second, very unhappy patrol.",
    ],
}

_mock_dm_counters: dict[str, int] = {"high": 0, "mid": 0, "low": 0}


def _mock_dm_reaction(roll: int) -> str:
    tier = "high" if roll >= 15 else ("low" if roll <= 5 else "mid")
    lines = _MOCK_DM_REACTIONS[tier]
    idx = _mock_dm_counters[tier]
    _mock_dm_counters[tier] = idx + 1
    return lines[idx % len(lines)]


def generate_dm_reaction(name: str, dialogue: str, roll: int) -> str:
    """Ask the DM model to narrate the outcome of this turn's d20 roll."""
    if MOCK_MODE:
        return _mock_dm_reaction(roll)

    prompt = f'Character: {name}. They just said: "{dialogue}". d20 roll: {roll}.'
    if _use_claude_for_dialogue():
        resp = _get_anthropic().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            system=_DM_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.content[0].text or "").strip()
    resp = _get_openai().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _DM_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=80,
    )
    return resp.choices[0].message.content.strip()


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
        self.last_audio_bytes: bytes | None = None  # previous turn's audio (for native speech models)

    def get_opening(self) -> str:
        """Return the DM's opening narration."""
        self.started = True
        return DM_NARRATION

    def next_turn(self) -> tuple[str, str, bytes, str, int]:
        """Execute the next character's turn.

        Returns:
            (character_name, dialogue_text, audio_bytes, dm_narration, roll)
        """
        agent = self.agents[self.turn_index % len(self.agents)]
        logger.info("Generating turn for %s", agent.name)

        roll = roll_d20()

        # Generate dialogue + audio (native speech or legacy text+TTS path)
        dialogue, audio_bytes = generate_turn_audio(agent, self.history, self.last_audio_bytes)

        # DM narrates the outcome of the roll
        dm_text = generate_dm_reaction(agent.name, dialogue, roll)

        # Store audio so the next character can "hear" it
        self.last_audio_bytes = audio_bytes

        # Update conversation history — the other agent sees this as a "user" message
        self.history.append({
            "role": "user",
            "content": f"[{agent.name}]: {dialogue}",
        })

        self.turn_index += 1
        return agent.name, dialogue, audio_bytes, dm_text, roll
