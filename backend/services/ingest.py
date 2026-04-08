import asyncio
import uuid
import hashlib
from datetime import datetime
from pathlib import Path

import aiosqlite

from backend.config import LINKS_DIR, NOTES_DIR
from backend.db import upsert_note, get_all_notes
from backend.services.scraper import scrape_url
from backend.services.summarizer import summarize
from backend.services.tts import generate_tts
from backend.services.enricher import enrich_stubs_batch


def _slugify(title: str) -> str:
    import re
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80]


def _build_markdown(title: str, source_url: str, summary_md: str,
                    tags: list, entities: list, domain: str,
                    audio_path: str, created: str) -> str:
    tags_yaml = ", ".join(f'"{t}"' for t in tags) if tags else ""
    entities_yaml = ", ".join(f'"{e}"' for e in entities) if entities else ""
    audio_rel = audio_path if audio_path else ""

    frontmatter = f"""---
title: "{title}"
source: "{source_url}"
created: "{created}"
tags: [{tags_yaml}]
entities: [{entities_yaml}]
domain: "{domain}"
audio: "{audio_rel}"
---

"""
    return frontmatter + summary_md


async def ingest(db: aiosqlite.Connection, url: str = None, text: str = None,
                 title: str = None) -> dict:
    """
    Full pipeline:
      1. Scrape URL (if url provided) or use raw text
      2. Summarize with Claude
      3. Generate TTS audio
      4. Write markdown to vault
      5. Save metadata to DB
    Returns the note dict.
    """
    if not url and not text:
        raise ValueError("Either url or text must be provided")

    note_id = str(uuid.uuid4())
    created = datetime.utcnow().strftime("%Y-%m-%d")

    # --- Step 1: Get content ---
    if url:
        page = await scrape_url(url)
        raw_title = title or page.title
        raw_text = page.text
        source_url = url
        target_dir = LINKS_DIR
    else:
        raw_title = title or "Untitled Note"
        raw_text = text
        source_url = ""
        target_dir = NOTES_DIR

    # --- Step 2: Summarize ---
    result = await summarize(raw_title, raw_text, source_url)
    final_title = result["frontmatter"].get("title") or raw_title
    entities = result["entities"]
    tags = result["tags"]
    domain = result["domain"]
    triples = result["triples"]

    # --- Step 3: TTS (non-fatal) ---
    audio_file = await generate_tts(note_id, result["markdown"])
    audio_rel = f"audio/{note_id}.mp3" if audio_file else None

    # --- Step 4: Write markdown ---
    slug = _slugify(final_title)
    md_path = target_dir / f"{slug}.md"

    # Avoid overwriting — append suffix if needed
    counter = 1
    while md_path.exists():
        md_path = target_dir / f"{slug}-{counter}.md"
        counter += 1

    full_md = _build_markdown(
        title=final_title,
        source_url=source_url,
        summary_md=result["markdown"],
        tags=tags,
        entities=entities,
        domain=domain,
        audio_path=audio_rel,
        created=created,
    )
    md_path.write_text(full_md, encoding="utf-8")

    # Also create concept stub pages for each entity
    thin_stubs = await _ensure_concept_stubs(entities, final_title, md_path)
    # Enrich new stubs that have no source-extracted context — fire and forget
    if thin_stubs:
        asyncio.create_task(enrich_stubs_batch(thin_stubs))

    # --- Step 5: Save to DB ---
    file_path_rel = str(md_path.relative_to(md_path.parent.parent.parent))
    content_hash = hashlib.md5(full_md.encode()).hexdigest()

    note = {
        "id": note_id,
        "title": final_title,
        "source_url": source_url or None,
        "file_path": file_path_rel,
        "tags": tags,
        "entities": entities,
        "domain": domain,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "audio_path": audio_rel,
        "summary_version": 1,
        "content_hash": content_hash,
    }
    await upsert_note(db, note)

    # Store triples as edges (async, non-blocking failure)
    try:
        from backend.services.graph import store_triples
        await store_triples(db, note_id, entities, triples)
    except Exception:
        pass

    return note


def _extract_context_sentences(source_path: Path, entity: str, max_sentences: int = 2) -> str:
    """
    Extract up to max_sentences sentences from source_path that mention entity.
    Strips YAML frontmatter and JSON blocks before searching.
    Returns empty string on any failure.
    """
    import re as _re
    try:
        text = source_path.read_text(encoding="utf-8")
        # Strip YAML frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            text = text[end + 3:] if end != -1 else text
        # Strip JSON code blocks
        text = _re.sub(r"```json[\s\S]*?```", "", text)
        # Flatten to single line for sentence splitting
        text = text.replace("\n", " ")
        sentences = _re.split(r"(?<=[.!?])\s+", text)
        matches = [
            s.strip() for s in sentences
            if entity.lower() in s.lower() and len(s.strip()) > 20
        ]
        return " ".join(matches[:max_sentences])
    except Exception:
        return ""


async def _ensure_concept_stubs(
    entities: list[str], source_title: str, source_path: Path
) -> list[tuple[Path, str]]:
    """
    Create or update concept stub pages for each entity wikilink.

    Returns a list of (stub_path, entity) for newly created stubs that have
    no extracted context — these are candidates for LLM enrichment.
    """
    from backend.config import CONCEPTS_DIR
    import re

    thin_stubs: list[tuple[Path, str]] = []

    for entity in entities:
        slug = re.sub(r"[^\w\s-]", "", entity.lower())
        slug = re.sub(r"[\s]+", "-", slug).strip("-")
        stub_path = CONCEPTS_DIR / f"{slug}.md"

        if stub_path.exists():
            content = stub_path.read_text(encoding="utf-8")
            backlink = f"- [[{source_title}]]"
            if backlink not in content:
                updated = content + f"\n{backlink}"
                # Upgrade placeholder once concept is well-referenced (3+ notes)
                mention_count = updated.count("- [[")
                placeholder = "> Auto-generated concept page. Edit to add your own notes."
                if mention_count >= 3 and placeholder in updated:
                    updated = updated.replace(
                        placeholder,
                        f"> Concept referenced across {mention_count} notes in this knowledge base.",
                        1,
                    )
                stub_path.write_text(updated, encoding="utf-8")
        else:
            # Context sentences from source note (no LLM call)
            context = _extract_context_sentences(source_path, entity)
            context_section = f"\n## Context\n\n> {context}\n" if context else ""

            # Co-occurrence: link to sibling entities from the same note
            peers = [e for e in entities if e.lower() != entity.lower()]
            related_section = ""
            if peers:
                peer_links = "\n".join(f"- [[{p}]]" for p in peers)
                related_section = f"\n## Related Concepts\n\n{peer_links}\n"

            stub_content = f"""---
title: "{entity}"
type: "concept"
aliases: []
---

# {entity}

## Overview

> Auto-generated concept page. Edit to add your own notes.
{context_section}
## Mentioned In

- [[{source_title}]]
{related_section}"""
            stub_path.write_text(stub_content, encoding="utf-8")

            # Queue for LLM enrichment only if there are no context sentences
            if not context:
                thin_stubs.append((stub_path, entity))

    return thin_stubs
