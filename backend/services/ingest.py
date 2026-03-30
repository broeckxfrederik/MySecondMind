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

    # --- Step 3: TTS ---
    audio_file = await generate_tts(note_id, result["markdown"])
    audio_rel = f"audio/{note_id}.mp3"

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
    await _ensure_concept_stubs(entities, final_title, md_path)

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


async def _ensure_concept_stubs(entities: list[str], source_title: str, source_path: Path):
    """Create minimal concept stub pages for each entity wikilink."""
    from backend.config import CONCEPTS_DIR

    for entity in entities:
        import re
        slug = re.sub(r"[^\w\s-]", "", entity.lower())
        slug = re.sub(r"[\s]+", "-", slug).strip("-")
        stub_path = CONCEPTS_DIR / f"{slug}.md"

        if stub_path.exists():
            # Append backlink if not already present
            content = stub_path.read_text(encoding="utf-8")
            backlink = f"- [[{source_title}]]"
            if backlink not in content:
                stub_path.write_text(content + f"\n{backlink}", encoding="utf-8")
        else:
            stub_content = f"""---
title: "{entity}"
type: "concept"
aliases: []
---

# {entity}

## Overview

> Auto-generated concept page. Edit to add your own notes.

## Mentioned In

- [[{source_title}]]
"""
            stub_path.write_text(stub_content, encoding="utf-8")
