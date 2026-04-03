"""
Idle cross-validator: when the system has been idle for a while, all available
providers independently summarize the same note, then peer-review each other's
output in a round-robin, and finally synthesize a consensus best result.

Round structure (with 3 providers A, B, C):
  Round 1 — Independent:  A, B, C each summarize the same content in parallel
  Round 2 — Peer review:  A reviews B+C  |  B reviews A+C  |  C reviews A+B
  Round 3 — Synthesis:    One provider reads all summaries + all reviews,
                          produces the final best-of-all note

The final synthesis is saved alongside the original note so the user can
compare. The full process is logged to ai-knowledge/cross-validation.md.

Configuration (via .env):
  IDLE_AFTER_MINUTES      — inactivity before triggering (default 60)
  VALIDATE_INTERVAL_MINUTES — how often the loop checks (default 30)
"""
import asyncio
import difflib
import random
import time
from datetime import datetime

from backend.config import (
    AI_KNOWLEDGE_DIR,
    IDLE_AFTER_MINUTES,
    VALIDATE_INTERVAL_MINUTES,
)
from backend.services.llm import complete, available_providers
from backend.services.summarizer import _load_preferences, _build_system_prompt

VALIDATION_LOG = AI_KNOWLEDGE_DIR / "cross-validation.md"

_last_activity: float = 0.0


# ── Activity tracking ──────────────────────────────────────────────────────────

def record_activity():
    global _last_activity
    _last_activity = time.monotonic()


def _seconds_since_activity() -> float:
    if _last_activity == 0.0:
        return float("inf")
    return time.monotonic() - _last_activity


# ── Helpers ────────────────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _ensure_log():
    if not VALIDATION_LOG.exists():
        VALIDATION_LOG.write_text(
            "# Cross-Validation Log\n\n"
            "> When idle, all providers independently summarize the same note,\n"
            "> peer-review each other, then synthesize a consensus best result.\n\n",
            encoding="utf-8",
        )


async def _pick_recent_note() -> dict | None:
    from backend.db import get_db, get_all_notes
    async with await get_db() as db:
        notes = await get_all_notes(db)
    if not notes:
        return None
    return random.choice(notes[:20])


# ── Validation rounds ──────────────────────────────────────────────────────────

async def _round1_summarize(providers: list[str], system: str,
                             messages: list[dict]) -> dict[str, str]:
    """
    Round 1: all providers summarize in parallel.
    Returns {provider_name: summary_text}
    """
    async def one(p: str) -> tuple[str, str]:
        text, used = await complete(system, messages, max_tokens=1500, preferred_provider=p)
        return used, text

    tasks = [one(p) for p in providers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    outputs: dict[str, str] = {}
    for r in results:
        if isinstance(r, Exception):
            print(f"[validator] Round 1 error: {r}")
            continue
        name, text = r
        outputs[name] = text

    return outputs


async def _round2_peer_review(summaries: dict[str, str]) -> dict[str, str]:
    """
    Round 2: each provider reviews the OTHER providers' summaries.
    Returns {reviewer_name: review_text}
    """
    names = list(summaries.keys())

    review_system = (
        "You are a critical peer reviewer for a personal knowledge management system. "
        "You review AI-generated knowledge summaries and give concise, actionable feedback. "
        "Focus on: accuracy, completeness of key ideas, quality of [[wikilinks]], "
        "clarity, and adherence to the structured format (TL;DR / Key Ideas / Connections)."
    )

    async def review_one(reviewer: str) -> tuple[str, str]:
        others = {k: v for k, v in summaries.items() if k != reviewer}
        sections = "\n\n".join(
            f"=== Summary by {name} ===\n{text[:2000]}"
            for name, text in others.items()
        )
        prompt = (
            f"You are `{reviewer}`. Review the following summaries written by other AI providers "
            f"for the same knowledge note.\n\n"
            f"{sections}\n\n"
            f"For each summary:\n"
            f"- What does it do well?\n"
            f"- What is missing, wrong, or could be improved?\n"
            f"- Rate it: Excellent / Good / Needs Work\n\n"
            f"Be specific and brief (3–5 bullet points per summary). No preamble."
        )
        text, used = await complete(
            review_system,
            [{"role": "user", "content": prompt}],
            max_tokens=600,
            preferred_provider=reviewer,
        )
        return used, text

    tasks = [review_one(p) for p in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    reviews: dict[str, str] = {}
    for r in results:
        if isinstance(r, Exception):
            print(f"[validator] Round 2 error: {r}")
            continue
        name, text = r
        reviews[name] = text

    return reviews


async def _round3_synthesize(summaries: dict[str, str], reviews: dict[str, str],
                              title: str, synthesizer: str) -> str:
    """
    Round 3: one provider reads all summaries + all reviews and produces
    the final best-of-all note.
    """
    preferences = _load_preferences()

    summaries_block = "\n\n".join(
        f"=== {name}'s summary ===\n{text[:2000]}"
        for name, text in summaries.items()
    )
    reviews_block = "\n\n".join(
        f"=== {name}'s peer review ===\n{text[:1000]}"
        for name, text in reviews.items()
    )

    system = (
        "You are synthesizing multiple AI-generated summaries into the single best version. "
        "You have access to all summaries and all peer reviews. "
        "Your output must be a complete, polished knowledge note — not a meta-commentary.\n\n"
        f"User preferences to follow:\n{preferences}"
    )

    prompt = (
        f"Synthesize the best possible knowledge note for: **{title}**\n\n"
        f"You have these independent summaries:\n\n{summaries_block}\n\n"
        f"And these peer reviews (what each provider thought of the others):\n\n{reviews_block}\n\n"
        f"Instructions:\n"
        f"- Incorporate the strongest elements from each summary\n"
        f"- Apply the corrections raised in the reviews\n"
        f"- Produce one complete, final markdown note with YAML frontmatter\n"
        f"- Do not mention the validation process in the output — write as if it's a direct summary\n"
        f"- Follow the user preferences above strictly"
    )

    final, _ = await complete(
        system,
        [{"role": "user", "content": prompt}],
        max_tokens=2000,
        preferred_provider=synthesizer,
    )
    return final


# ── Main validation pass ───────────────────────────────────────────────────────

async def _validate_once():
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
    title = note["title"]
    source_url = note.get("source_url") or ""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    print(f"[validator] Starting 3-round validation for: {title!r}")
    print(f"[validator] Providers: {providers}")

    # Build shared prompt
    preferences = _load_preferences()
    system = _build_system_prompt(preferences)
    user_msg = (
        f"Please summarize the following content into a structured knowledge note.\n\n"
        f"Title: {title}\nSource: {source_url or 'direct input'}\n\n---\n"
        f"{original_content[:6000]}\n---\n\n"
        f"Produce the full markdown note following the system instructions."
    )
    messages = [{"role": "user", "content": user_msg}]

    # ── Round 1: independent summaries ────────────────────────────────────────
    print("[validator] Round 1: independent summaries...")
    summaries = await _round1_summarize(providers, system, messages)
    if len(summaries) < 2:
        print("[validator] Not enough successful summaries — aborting")
        return

    # ── Round 2: peer reviews ─────────────────────────────────────────────────
    print("[validator] Round 2: peer reviews...")
    reviews = await _round2_peer_review(summaries)

    # ── Round 3: synthesis ────────────────────────────────────────────────────
    # Use the first available provider (preferred) as synthesizer
    synthesizer = providers[0] if providers[0] in summaries else list(summaries.keys())[0]
    print(f"[validator] Round 3: synthesis by {synthesizer}...")
    final_note = await _round3_synthesize(summaries, reviews, title, synthesizer)

    # ── Save synthesis alongside original ────────────────────────────────────
    validated_path = fp.with_stem(fp.stem + "-validated")
    validated_path.write_text(final_note, encoding="utf-8")
    print(f"[validator] Synthesis saved to {validated_path.name}")

    # ── Compute pairwise similarities ─────────────────────────────────────────
    provider_list = list(summaries.keys())
    sim_lines = []
    for i, pa in enumerate(provider_list):
        for pb in provider_list[i + 1:]:
            sim = _similarity(summaries[pa], summaries[pb])
            sim_lines.append(f"`{pa}` vs `{pb}`: {sim:.1%}")

    # ── Log to cross-validation.md ────────────────────────────────────────────
    _ensure_log()

    summaries_section = "\n\n".join(
        f"<details><summary>{name} summary</summary>\n\n"
        f"```markdown\n{text[:1500]}\n{'[truncated]' if len(text) > 1500 else ''}\n```\n\n</details>"
        for name, text in summaries.items()
    )

    reviews_section = "\n\n".join(
        f"**{name}'s reviews:**\n\n{text.strip()}"
        for name, text in reviews.items()
    )

    entry = (
        f"\n## {timestamp} — `{title[:60]}`\n\n"
        f"**Providers:** {', '.join(f'`{p}`' for p in summaries)}\n\n"
        f"### Pairwise Similarity\n\n"
        + "\n".join(f"- {s}" for s in sim_lines) +
        f"\n\n### Round 1 — Independent Summaries\n\n{summaries_section}\n\n"
        f"### Round 2 — Peer Reviews\n\n{reviews_section}\n\n"
        f"### Round 3 — Final Synthesis (by `{synthesizer}`)\n\n"
        f"Saved to: `{validated_path.name}`\n\n"
        f"<details><summary>View synthesis</summary>\n\n"
        f"```markdown\n{final_note[:2000]}\n{'[truncated]' if len(final_note) > 2000 else ''}\n```\n\n"
        f"</details>\n\n---"
    )

    existing = VALIDATION_LOG.read_text(encoding="utf-8")
    VALIDATION_LOG.write_text(existing + entry, encoding="utf-8")
    print(f"[validator] Done. Similarities: {' | '.join(sim_lines)}")


# ── Background loop ────────────────────────────────────────────────────────────

async def validation_loop():
    interval = VALIDATE_INTERVAL_MINUTES * 60
    idle_threshold = IDLE_AFTER_MINUTES * 60

    print(
        f"[validator] Started. Will validate after {IDLE_AFTER_MINUTES}m idle, "
        f"checking every {VALIDATE_INTERVAL_MINUTES}m."
    )

    while True:
        await asyncio.sleep(interval)
        idle = _seconds_since_activity()
        if idle >= idle_threshold:
            print(f"[validator] Idle {idle / 60:.1f}m — running 3-round validation")
            try:
                await _validate_once()
            except Exception as e:
                print(f"[validator] Error: {e}")
        else:
            print(f"[validator] Not idle enough ({idle / 60:.1f}m < {IDLE_AFTER_MINUTES}m)")
