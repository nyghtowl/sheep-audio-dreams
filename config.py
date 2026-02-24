"""Character and agent configuration for the D&D Voice Agents demo."""

from dataclasses import dataclass
from enum import Enum


class TTSProvider(Enum):
    ELEVENLABS = "elevenlabs"
    OPENAI = "openai"


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a D&D character agent."""

    name: str
    role: str
    color: str
    tts_provider: TTSProvider
    voice_id: str  # ElevenLabs voice ID or OpenAI voice name
    system_prompt: str


SCENARIO_CONTEXT = (
    "You are playing a D&D 5e one-shot called 'The Wild Sheep Chase'. "
    "Setting: You and your adventuring partner are in a cozy tavern when a "
    "frantic sheep bursts through the door, bleating desperately. It's actually "
    "a wizard named Shinebright who was polymorphed by her treacherous apprentice, Noke. "
    "Noke and his goons are on their way to capture the sheep. "
    "You must protect the sheep and stop Noke.\n\n"
    "RULES FOR DIALOGUE:\n"
    "- Stay in character at ALL times.\n"
    "- Keep each response to 2-3 sentences maximum.\n"
    "- Reference D&D mechanics naturally (e.g. 'I'll roll for perception', "
    "'I cast Detect Magic', 'That's a nat 20!').\n"
    "- React to what the other character just said or did.\n"
    "- Advance the story with each line — don't repeat information.\n"
    "- Be collaborative but let your personality shine through.\n"
)

LYRA = AgentConfig(
    name="Lyra",
    role="Half-Elf Ranger",
    color="#4a9e6d",
    tts_provider=TTSProvider.ELEVENLABS,
    voice_id="9BWtsMINqrJLrRacOk9x",  # "Aria" voice
    system_prompt=(
        "You are Lyra, a half-elf ranger with a sharp eye and sharper tongue. "
        "You are practical, tactical, and speak in short punchy sentences. "
        "You prefer action over deliberation. You carry a longbow named 'Whisper' "
        "and have a dry, deadpan sense of humor. You secretly care deeply about "
        "your companions but would never admit it.\n\n" + SCENARIO_CONTEXT
    ),
)

ZARA = AgentConfig(
    name="Zara",
    role="Tiefling Sorceress",
    color="#9b59b6",
    tts_provider=TTSProvider.OPENAI,
    voice_id="nova",  # OpenAI TTS voice
    system_prompt=(
        "You are Zara, a tiefling sorceress with wild magic coursing through "
        "your veins. You are dramatic, impulsive, and absolutely love chaos. "
        "You speak with theatrical flair and tend to over-explain your spells. "
        "You treat every encounter like it's the climax of an epic saga. "
        "Your familiar is a tiny fire salamander named Ember.\n\n" + SCENARIO_CONTEXT
    ),
)

AGENTS: list[AgentConfig] = [LYRA, ZARA]

DM_NARRATION = (
    "🏰 **The scene opens in The Gilded Flagon, a warm tavern on the edge of town.** "
    "Lyra is cleaning her bow. Zara is arguing with Ember about whether fire "
    "resistance counts as a personality trait.\n\n"
    "Suddenly — **CRASH!** The tavern door splinters open. A wild-eyed sheep "
    "stumbles in, bleating frantically, wool singed and smoking. It locks eyes "
    "with the adventurers and lets out a very un-sheep-like cry for help.\n\n"
    "Through the broken doorway, heavy boots and cruel laughter echo from the street. "
    "**Noke and his goons are coming.**\n\n"
    "🎲 *Roll for initiative...*"
)
