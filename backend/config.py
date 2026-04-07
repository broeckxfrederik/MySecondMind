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

# ── LLM providers ──────────────────────────────────────────────────────────────

# Anthropic / Claude
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Groq (free tier, very fast — Llama 3.3 70B recommended)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Google Gemini (free tier: 1500 req/day)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Provider priority order (first = preferred, fallback left-to-right on rate limit)
# Only providers with a configured API key are actually used
_raw_order = os.getenv("PROVIDERS", "anthropic,groq,gemini")
PROVIDER_ORDER: list[str] = [p.strip() for p in _raw_order.split(",") if p.strip()]

# Seconds a provider stays in cooldown after hitting a rate limit
PROVIDER_COOLDOWN_SECONDS = int(os.getenv("PROVIDER_COOLDOWN_SECONDS", "60"))

# ── TTS ────────────────────────────────────────────────────────────────────────
TTS_VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")

# ── Learning loop ──────────────────────────────────────────────────────────────
EDITS_BEFORE_CONSOLIDATION = int(os.getenv("EDITS_BEFORE_CONSOLIDATION", "10"))

# ── Idle cross-validation ──────────────────────────────────────────────────────
# How many minutes of inactivity before triggering a cross-validation pass
IDLE_AFTER_MINUTES = int(os.getenv("IDLE_AFTER_MINUTES", "60"))
# How often the validator loop wakes up to check (minutes)
VALIDATE_INTERVAL_MINUTES = int(os.getenv("VALIDATE_INTERVAL_MINUTES", "30"))

# ── Ensure dirs exist ──────────────────────────────────────────────────────────
for d in [VAULT_DIR, AUDIO_DIR, AI_KNOWLEDGE_DIR, DATA_DIR,
          LINKS_DIR, NOTES_DIR, CONCEPTS_DIR, CANVAS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
