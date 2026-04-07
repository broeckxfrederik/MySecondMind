"""
Learning service: analyzes user edits and updates AI knowledge files.

Flow:
  1. analyze_edits()        — per-edit: infer what the edit reveals about preferences
  2. consolidate_preferences() — every N edits: rewrite user-preferences.md from patterns
"""
from datetime import datetime

import aiosqlite

from backend.config import USER_PREFS_FILE, LEARNED_PATTERNS_FILE
from backend.db import get_unanalyzed_edits, mark_edits_analyzed
from backend.services.llm import complete


async def analyze_edits(db: aiosqlite.Connection):
    """
    For each unanalyzed edit, infer what it reveals about user preferences.
    Appends findings to learned-patterns.md.
    """
    edits = await get_unanalyzed_edits(db)
    if not edits:
        return

    analyzed_ids = []
    new_patterns = []

    for edit in edits:
        try:
            prompt = (
                "A user edited an AI-generated knowledge note. Here is what changed:\n\n"
                f"---\n{edit['diff'][:3000]}\n---\n\n"
                "Based only on this diff, infer 1–3 specific, actionable preferences "
                "the user has about how they want their notes written.\n"
                "Be concrete. Examples:\n"
                "- User removed the TL;DR section — they prefer starting directly with Key Ideas\n"
                "- User replaced passive voice with active constructions\n"
                "- User added more [[wikilinks]] to connect concepts\n"
                "- User shortened the summary — prefers ≤2 paragraphs per section\n\n"
                "Format your response as a short bullet list. No preamble."
            )
            insight, provider = await complete(
                system="You analyze user edits to AI-generated notes to extract writing preferences.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
            )
            new_patterns.append(
                f"\n### Edit on {edit['timestamp'][:10]} (via {provider})\n{insight.strip()}"
            )
            analyzed_ids.append(edit["id"])
        except Exception as e:
            print(f"[learner] Failed to analyze edit {edit['id']}: {e}")

    if new_patterns:
        existing = LEARNED_PATTERNS_FILE.read_text(encoding="utf-8")
        LEARNED_PATTERNS_FILE.write_text(
            existing + "\n" + "\n".join(new_patterns), encoding="utf-8"
        )

    if analyzed_ids:
        await mark_edits_analyzed(db, analyzed_ids)


async def consolidate_preferences(db: aiosqlite.Connection):
    """
    Synthesizes all learned patterns into user-preferences.md.
    Called every EDITS_BEFORE_CONSOLIDATION edits.
    """
    await analyze_edits(db)

    patterns_text = LEARNED_PATTERNS_FILE.read_text(encoding="utf-8")
    current_prefs = USER_PREFS_FILE.read_text(encoding="utf-8")

    prompt = (
        "You are updating a 'user preferences' file for an AI knowledge management system.\n\n"
        f"Current preferences:\n---\n{current_prefs}\n---\n\n"
        f"Observed edit patterns (what the user actually changes in AI-generated notes):\n"
        f"---\n{patterns_text[-4000:]}\n---\n\n"
        "Produce an updated version of the user preferences file that:\n"
        "1. Keeps everything that still holds\n"
        "2. Updates rules that the edit patterns contradict or refine\n"
        "3. Adds new rules for patterns you see consistently\n"
        "4. Removes rules that seem wrong based on observed edits\n\n"
        "Keep the same structure and format. Output ONLY the updated file content."
    )

    updated_prefs, provider = await complete(
        system="You maintain user preference files for a knowledge management system.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
    )

    backup = USER_PREFS_FILE.parent / f"user-preferences-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.md"
    backup.write_text(current_prefs, encoding="utf-8")
    USER_PREFS_FILE.write_text(updated_prefs.strip(), encoding="utf-8")
    print(f"[learner] Preferences consolidated via {provider}. Backup: {backup.name}")

    LEARNED_PATTERNS_FILE.write_text(
        "# Learned Patterns from User Edits\n\n"
        "> Appended automatically. Consolidated into user-preferences.md every N edits.\n\n"
        "## Edit Log\n\n<!-- Entries appended here automatically by learner.py -->\n",
        encoding="utf-8",
    )
