"""
Learning service: analyzes user edits and updates AI knowledge files.

Flow:
  1. analyze_edits()     — per-edit: ask Claude what the edit reveals about preferences
  2. consolidate_preferences() — every N edits: summarize all patterns into user-preferences.md
"""
from datetime import datetime

import anthropic
import aiosqlite

from backend.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    USER_PREFS_FILE, LEARNED_PATTERNS_FILE, DOMAIN_INTERESTS_FILE,
)
from backend.db import get_unanalyzed_edits, mark_edits_analyzed


async def analyze_edits(db: aiosqlite.Connection):
    """
    For each unanalyzed edit, ask Claude what it reveals about the user's preferences.
    Appends findings to learned-patterns.md.
    """
    edits = await get_unanalyzed_edits(db)
    if not edits:
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    analyzed_ids = []
    new_patterns = []

    for edit in edits:
        try:
            prompt = f"""A user edited an AI-generated knowledge note. Here is what changed:

---
{edit['diff'][:3000]}
---

Based only on this diff, infer 1–3 specific, actionable preferences the user has about how they want their notes written.
Be concrete. Examples of good insights:
- "User removed the TL;DR section — they prefer starting directly with Key Ideas"
- "User replaced passive voice with active constructions"
- "User added more [[wikilinks]] to connect concepts"
- "User shortened the summary — prefers ≤2 paragraphs per section"
- "User reorganized: moved Connections section before Key Ideas"

Format your response as a short bullet list of insights. No preamble."""

            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            insight = message.content[0].text.strip()
            new_patterns.append(f"\n### Edit on {edit['timestamp'][:10]}\n{insight}")
            analyzed_ids.append(edit["id"])
        except Exception as e:
            print(f"[learner] Failed to analyze edit {edit['id']}: {e}")

    if new_patterns:
        existing = LEARNED_PATTERNS_FILE.read_text(encoding="utf-8")
        LEARNED_PATTERNS_FILE.write_text(
            existing + "\n" + "\n".join(new_patterns),
            encoding="utf-8"
        )

    if analyzed_ids:
        await mark_edits_analyzed(db, analyzed_ids)


async def consolidate_preferences(db: aiosqlite.Connection):
    """
    Synthesizes all learned patterns into user-preferences.md.
    Called every EDITS_BEFORE_CONSOLIDATION edits.
    """
    # First analyze any pending edits
    await analyze_edits(db)

    patterns_text = LEARNED_PATTERNS_FILE.read_text(encoding="utf-8")
    current_prefs = USER_PREFS_FILE.read_text(encoding="utf-8")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are updating a "user preferences" file for an AI knowledge management system.

Current preferences:
---
{current_prefs}
---

Observed edit patterns (what the user actually changes in AI-generated notes):
---
{patterns_text[-4000:]}
---

Produce an updated version of the user preferences file that:
1. Keeps everything that still holds
2. Updates rules that the edit patterns contradict or refine
3. Adds new rules for patterns you see consistently
4. Removes rules that seem wrong based on observed edits

Keep the same structure and format as the current preferences file.
Output ONLY the updated file content — no explanation, no preamble."""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    updated_prefs = message.content[0].text.strip()

    # Back up old preferences with timestamp
    backup_path = USER_PREFS_FILE.parent / f"user-preferences-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.md"
    backup_path.write_text(current_prefs, encoding="utf-8")

    USER_PREFS_FILE.write_text(updated_prefs, encoding="utf-8")
    print(f"[learner] Preferences consolidated. Backup at {backup_path.name}")

    # Clear the learned patterns log (keep header)
    LEARNED_PATTERNS_FILE.write_text(
        "# Learned Patterns from User Edits\n\n"
        "> This file is appended to automatically as the system observes edits the user makes to generated summaries.\n"
        "> Every 10 edits, these patterns are consolidated into user-preferences.md.\n\n"
        "## Edit Log\n\n<!-- Entries appended here automatically by learner.py -->\n",
        encoding="utf-8"
    )
