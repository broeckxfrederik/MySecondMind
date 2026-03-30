import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent

# Storage paths
VAULT_DIR = BASE_DIR / "vault"
AUDIO_DIR = BASE_DIR / "audio"
AI_KNOWLEDGE_DIR = BASE_DIR / "ai-knowledge"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "mysecondmind.db"

# Subdirs in vault
LINKS_DIR = VAULT_DIR / "links"
NOTES_DIR = VAULT_DIR / "notes"
CONCEPTS_DIR = VAULT_DIR / "concepts"
CANVAS_DIR = VAULT_DIR / "canvas"

# AI knowledge files
USER_PREFS_FILE = AI_KNOWLEDGE_DIR / "user-preferences.md"
LEARNED_PATTERNS_FILE = AI_KNOWLEDGE_DIR / "learned-patterns.md"
DOMAIN_INTERESTS_FILE = AI_KNOWLEDGE_DIR / "domain-interests.md"
SESSION_NOTES_FILE = AI_KNOWLEDGE_DIR / "session-notes.md"

# API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# TTS
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")  # Natural British male voice

# Learning loop
EDITS_BEFORE_CONSOLIDATION = int(os.getenv("EDITS_BEFORE_CONSOLIDATION", "10"))

# Ensure all dirs exist
for d in [VAULT_DIR, AUDIO_DIR, AI_KNOWLEDGE_DIR, DATA_DIR, LINKS_DIR, NOTES_DIR, CONCEPTS_DIR, CANVAS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
