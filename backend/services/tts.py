import asyncio
import re
from pathlib import Path
import edge_tts
from backend.config import TTS_VOICE, AUDIO_DIR


def _strip_markdown(text: str) -> str:
    """Strip markdown syntax for cleaner TTS output."""
    # Remove frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2]

    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)

    # Remove headings markers but keep text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Remove wikilinks — keep the text inside
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)

    # Remove markdown links
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # Remove bold/italic
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)

    # Remove horizontal rules
    text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)

    # Remove trailing JSON block
    text = re.sub(r"```json[\s\S]*?```", "", text)

    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def generate_tts(note_id: str, text: str) -> Path | None:
    """
    Generate an MP3 audio file for the given text. Returns the path, or None
    if TTS fails (e.g. upstream service outage). Ingest continues without audio.
    """
    clean_text = _strip_markdown(text)

    # Truncate to ~5000 chars for reasonable audio length
    if len(clean_text) > 5000:
        clean_text = clean_text[:5000] + " ... End of summary."

    audio_path = AUDIO_DIR / f"{note_id}.mp3"

    try:
        communicate = edge_tts.Communicate(clean_text, TTS_VOICE)
        await communicate.save(str(audio_path))
        return audio_path
    except Exception as e:
        print(f"[tts] Warning: TTS generation failed ({e}). Continuing without audio.")
        return None
