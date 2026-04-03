"""
Idle cross-validator: when the system hasn't been used for a while,
re-summarize a recent note with a *different* provider and compare outputs.

This lets the providers verify each other's quality and surfaces
disagreements worth investigating (logged to ai-knowledge/cross-validation.md).

Configuration:
  IDLE_AFTER_MINUTES   — how long without activity before running (default 60)
  VALIDATE_INTERVAL    — how often the background loop checks (default 30 min)
"""
import asyncio
import difflib
import random
from datetime import datetime
from pathlib import Path

from backend.config import (
    AI_KNOWLEDGE_DIR,
    IDLE_AFTER_MINUTES,
    VALIDATE_INTERVAL_MINUTES,
)
from backend.services.llm import complete, available_providers
from backend.services.summarizer import _load_preferences, _build_system_prompt

VALIDATION_LOG = AI_KNOWLEDGE_DIR / "cross-validation.md"

# Track last activity so validator knows when to run
_last_activity: float = 0.0


def record_activity():
    """Call this on every user-triggered action (ingest, graph rebuild, etc.)."""
    global _last_activity
    import time
    _last_activity = time.monotonic()


def _seconds_since_activity() -> float:
    import time
    if _last_activity == 0.0:
        return float("inf")
    return time.monotonic() - _last_activity


def _ensure_log():
    if not VALIDATION_LOG.exists():
        VALIDATION_LOG.write_text(
            "# Cross-Validation Log\n\n"
            "> When the system is idle, two providers re-summarize the same note\n"
            "> and their outputs are compared here.\n\n",
            encoding="utf-8",
        )


def _similarity(a: str, b: str) -> float:
    """Return a 0–1 similarity score between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def _build_diff(a: str, b: str, label_a: str, label_b: str) -> str:
    lines_a = a.splitlines(keepends=True)
    lines_b = b.splitlines(keepends=True)
    diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b, lineterm=""))
    if not diff:
        return "(outputs are identical)"
    # Cap diff at 60 lines to keep the log readable
    return "\n".join(diff[:60]) + ("\n[... diff truncated ...]" if len(diff) > 60 else "")


async def _pick_recent_note() -> dict | None:
    """Pick a random note from the last 20 most recently updated."""
    from backend.db import get_db, get_all_notes
    async with await get_db() as db:
        notes = await get_all_notes(db)
    if not notes:
        return None
    pool = notes[:20]
    return random.choice(pool)


async def _validate_once():
    """Run a single cross-validation pass."""
    providers = available_providers()
    if len(providers) < 2:
        print("[validator] Need at least 2 available providers — skipping")
        return

    note = await _pick_recent_note()
    if not note:
        return

    from backend.config import BASE_DIR
    fp = BASE_DIR / note["file_path"]
    if not fp.exists():
        return

    original_content = fp.read_text(encoding="utf-8")

    # Reconstruct the summarization prompt from the note's source
    source_url = note.get("source_url") or ""
    title = note["title"]

    # Use original content as the "text" (we're re-summarizing the existing summary
    # to compare style/structure, not re-scraping)
    preferences = _load_preferences()
    system = _build_system_prompt(preferences)
    user_msg = (
        f"Please summarize the following content into a structured knowledge note.\n\n"
        f"Title: {title}\nSource: {source_url or 'direct input'}\n\n---\n"
        f"{original_content[:6000]}\n---\n\n"
        f"Produce the full markdown note following the system instructions."
    )
    messages = [{"role": "user", "content": user_msg}]

    # Pick two different providers
    p1, p2 = providers[0], providers[1]

    try:
        text_a, used_a = await complete(system, messages, max_tokens=1500, preferred_provider=p1)
        text_b, used_b = await complete(system, messages, max_tokens=1500, preferred_provider=p2)
    except Exception as e:
        print(f"[validator] Completion failed: {e}")
        return

    similarity = _similarity(text_a, text_b)
    diff_block = _build_diff(text_a, text_b, used_a, used_b)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Ask a third provider (or fall back to p1) to judge the differences
    judge_system = (
        "You are reviewing two AI-generated knowledge summaries of the same content. "
        "Identify meaningful differences in quality, style, structure, or accuracy. "
        "Be brief — 2-4 bullet points max. Focus on what's actionable."
    )
    judge_prompt = (
        f"Two providers summarized the same note. Similarity score: {similarity:.2f}/1.0\n\n"
        f"=== {used_a} output ===\n{text_a[:2000]}\n\n"
        f"=== {used_b} output ===\n{text_b[:2000]}\n\n"
        f"What are the meaningful differences? Which is better and why?"
    )
    try:
        judge_provider = next((p for p in providers if p not in [used_a, used_b]), used_a)
        judgment, _ = await complete(
            judge_system,
            [{"role": "user", "content": judge_prompt}],
            max_tokens=400,
            preferred_provider=judge_provider,
        )
    except Exception:
        judgment = "(judgment unavailable — only one provider was accessible)"

    _ensure_log()
    entry = (
        f"\n## {timestamp} — `{title[:60]}`\n\n"
        f"**Providers compared:** `{used_a}` vs `{used_b}`  \n"
        f"**Similarity:** {similarity:.2%}  \n\n"
        f"### Judge's Assessment (`{judge_provider}`)\n\n"
        f"{judgment}\n\n"
        f"<details><summary>Full diff</summary>\n\n"
        f"```diff\n{diff_block}\n```\n\n"
        f"</details>\n\n---"
    )

    existing = VALIDATION_LOG.read_text(encoding="utf-8")
    VALIDATION_LOG.write_text(existing + entry, encoding="utf-8")
    print(f"[validator] Cross-validation complete. Similarity: {similarity:.2%}. Logged to cross-validation.md")


async def validation_loop():
    """
    Background asyncio task. Sleeps for VALIDATE_INTERVAL, then checks
    if the system has been idle long enough to run a cross-validation pass.
    """
    interval_seconds = VALIDATE_INTERVAL_MINUTES * 60
    idle_threshold = IDLE_AFTER_MINUTES * 60

    print(f"[validator] Started. Will cross-validate after {IDLE_AFTER_MINUTES}m idle, checking every {VALIDATE_INTERVAL_MINUTES}m.")

    while True:
        await asyncio.sleep(interval_seconds)
        idle = _seconds_since_activity()
        if idle >= idle_threshold:
            print(f"[validator] System idle for {idle/60:.1f}m — running cross-validation")
            try:
                await _validate_once()
            except Exception as e:
                print(f"[validator] Error during validation: {e}")
        else:
            print(f"[validator] Not idle enough ({idle/60:.1f}m < {IDLE_AFTER_MINUTES}m) — skipping")
