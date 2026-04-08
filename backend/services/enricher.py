"""
Concept stub enrichment.

When a concept stub has no real content (only the auto-generated placeholder),
this module asks the LLM for a brief 2-3 sentence TL;DR and writes it into the
stub, clearly marked as self-researched so it's never confused with the user's
own notes.

Enrichment is always non-blocking: callers fire tasks and move on.
"""
import asyncio
import re
from pathlib import Path

from backend.services.llm import complete

# Marker present in every brand-new stub that has no real content yet
_PLACEHOLDER = "> Auto-generated concept page. Edit to add your own notes."
# Marker we write so we never enrich the same stub twice
_SELF_RESEARCHED = "*Self-researched*"


def _is_thin(content: str) -> bool:
    """Return True if the stub still holds only the auto-generated placeholder."""
    return _PLACEHOLDER in content


async def _generate_tldr(entity: str) -> str | None:
    """
    Ask the LLM for a 2-3 sentence plain-prose explanation of entity.
    Returns None on any failure so callers can skip gracefully.
    """
    system = (
        "You are adding brief concept definitions to a personal knowledge base. "
        "Write exactly 2-3 factual, clear sentences about the concept or term given. "
        "Use plain prose only — no markdown headers, no bullet points, no bold text."
    )
    user = f"Explain what '{entity}' is in 2-3 sentences."
    try:
        raw, _ = await complete(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=120,
        )
        return raw.strip()
    except Exception:
        return None


async def enrich_stub(stub_path: Path, entity: str) -> bool:
    """
    Enrich a single concept stub with a self-researched TL;DR.

    Skips if:
    - File doesn't exist
    - Already enriched (no placeholder found)
    - LLM call fails

    Returns True if the stub was updated.
    """
    if not stub_path.exists():
        return False

    content = stub_path.read_text(encoding="utf-8")
    if not _is_thin(content):
        return False  # Already has real content or was already enriched

    tldr = await _generate_tldr(entity)
    if not tldr:
        return False

    enriched_block = (
        f"> {_SELF_RESEARCHED} — not sourced from your notes.\n\n"
        f"**TL;DR:** {tldr}"
    )
    content = content.replace(_PLACEHOLDER, enriched_block, 1)
    stub_path.write_text(content, encoding="utf-8")
    print(f"[enricher] Enriched: {entity}")
    return True


async def enrich_stubs_batch(stubs: list[tuple[Path, str]]):
    """
    Enrich a list of (stub_path, entity_name) pairs.

    Capped at 3 per auto-ingest call to avoid exhausting free-tier quotas.
    The /enrich-stubs endpoint calls enrich_all_thin_stubs() for bulk work.

    Waits 2 s before starting so the HTTP response that triggered ingest
    has already returned. Adds 0.5 s between calls to avoid rate-limit bursts.
    """
    stubs = stubs[:3]
    await asyncio.sleep(2)
    for stub_path, entity in stubs:
        try:
            await enrich_stub(stub_path, entity)
        except Exception as exc:
            print(f"[enricher] Skipped {entity}: {exc}")
        await asyncio.sleep(0.5)


async def enrich_all_thin_stubs(concepts_dir: Path):
    """
    Scan every stub in concepts_dir and enrich thin ones.
    Intended for the /enrich-stubs API endpoint (bulk retroactive pass).
    """
    stubs: list[tuple[Path, str]] = []
    for stub_path in sorted(concepts_dir.glob("*.md")):
        try:
            content = stub_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not _is_thin(content):
            continue
        m = re.search(r'^title:\s*"(.+?)"', content, re.MULTILINE)
        if not m:
            continue
        stubs.append((stub_path, m.group(1)))

    print(f"[enricher] {len(stubs)} thin stubs to enrich")
    for stub_path, entity in stubs:
        try:
            await enrich_stub(stub_path, entity)
        except Exception as exc:
            print(f"[enricher] Skipped {entity}: {exc}")
        await asyncio.sleep(0.5)
