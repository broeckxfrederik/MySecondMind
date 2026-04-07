import json
import re
from backend.config import USER_PREFS_FILE
from backend.services.llm import complete

# Characters per chunk — stays comfortably under Groq's 12k TPM with system prompt overhead
CHUNK_SIZE = 6000
# Only chunk if content exceeds this (single-pass is better quality when it fits)
CHUNK_THRESHOLD = 7000


def _load_preferences() -> str:
    if USER_PREFS_FILE.exists():
        prefs = USER_PREFS_FILE.read_text(encoding="utf-8")
        if len(prefs) > 3000:
            prefs = prefs[:3000] + "\n[... truncated ...]"
        return prefs
    return ""


def _build_system_prompt(preferences: str) -> str:
    return f"""You are the knowledge management layer of a personal "second mind" system.
Your job is to produce structured Obsidian-compatible markdown summaries.

Read and strictly apply the user's preferences below:

---
{preferences}
---

Always output a complete markdown note including:
1. YAML frontmatter (use the template from preferences)
2. ## TL;DR section
3. ## Key Ideas section
4. ## Connections section (how this links to other concepts, using [[wikilinks]])

Entity/concept extraction rules:
- Extract 3–8 key concepts as [[wikilinks]] inline in the text
- Use Title Case for wikilinks
- Focus on concepts, tools, methods, and ideas — not just names
- Also return a JSON block at the very end (after a horizontal rule) with extracted metadata:

---
```json
{{
  "title": "...",
  "tags": ["tag1", "tag2"],
  "entities": ["Entity1", "Entity2"],
  "domain": "technology",
  "triples": [
    {{"subject": "ConceptA", "predicate": "builds_on", "object": "ConceptB"}},
    {{"subject": "Person", "predicate": "created", "object": "Tool"}}
  ]
}}
```
"""


def _split_chunks(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """
    Split text into chunks at paragraph boundaries, targeting chunk_size chars.
    Never cuts mid-paragraph.
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks, current = [], []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > chunk_size and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += para_len + 2  # +2 for the \n\n separator

    if current:
        chunks.append("\n\n".join(current))

    return chunks


async def _summarize_chunk(chunk: str, idx: int, total: int, title: str) -> str:
    """Produce a concise bullet-point summary of one chunk (no full note format)."""
    system = (
        "You are extracting key information from a section of a longer article. "
        "Output concise bullet points covering the main ideas, facts, arguments, and any notable quotes. "
        "Do not write an introduction or conclusion. Just bullets. Be thorough but not padded."
    )
    user = (
        f"Article: {title}\nSection {idx + 1} of {total}:\n\n---\n{chunk}\n---\n\n"
        "Extract the key points as bullet points."
    )
    raw, _ = await complete(system=system, messages=[{"role": "user", "content": user}], max_tokens=800)
    return raw.strip()


def _parse_output(raw: str, title: str) -> dict:
    markdown_part = raw
    meta = {"title": title, "tags": [], "entities": [], "domain": "other", "triples": []}

    if "```json" in raw:
        parts = raw.rsplit("```json", 1)
        markdown_part = parts[0].rstrip("\n -")
        try:
            json_str = parts[1].split("```")[0].strip()
            meta.update(json.loads(json_str))
        except Exception:
            pass

    return markdown_part, meta


async def summarize(title: str, text: str, source_url: str = "") -> dict:
    """
    Map-reduce summarization:
    - Short content  (<= CHUNK_THRESHOLD chars): single LLM pass
    - Long content   (>  CHUNK_THRESHOLD chars): chunk → summarize each → merge → final pass

    Returns:
        {
            "markdown": str,
            "frontmatter": dict,
            "entities": list[str],
            "tags": list[str],
            "domain": str,
            "triples": list[dict],
            "provider": str,
        }
    """
    preferences = _load_preferences()
    system = _build_system_prompt(preferences)

    # ── Single pass (short content) ────────────────────────────────────────────
    if len(text) <= CHUNK_THRESHOLD:
        user_message = (
            f"Please summarize the following content into a structured knowledge note.\n\n"
            f"Title: {title}\nSource: {source_url or 'direct input'}\n\n---\n{text}\n---\n\n"
            f"Produce the full markdown note following the system instructions."
        )
        raw, provider = await complete(
            system=system,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=2048,
        )
        markdown_part, meta = _parse_output(raw, title)
        return {
            "markdown": markdown_part,
            "frontmatter": meta,
            "entities": meta.get("entities", []),
            "tags": meta.get("tags", []),
            "domain": meta.get("domain", "other"),
            "triples": meta.get("triples", []),
            "provider": provider,
        }

    # ── Map: summarize each chunk ──────────────────────────────────────────────
    chunks = _split_chunks(text)
    print(f"[summarizer] Long content ({len(text)} chars) → {len(chunks)} chunks")

    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        summary = await _summarize_chunk(chunk, i, len(chunks), title)
        chunk_summaries.append(f"### Section {i + 1}\n{summary}")

    # ── Reduce: merge chunk summaries into one final note ─────────────────────
    merged = "\n\n".join(chunk_summaries)
    user_message = (
        f"Below are section-by-section extracts from a longer article. "
        f"Synthesize them into a single cohesive knowledge note as if you had read the whole article.\n\n"
        f"Title: {title}\nSource: {source_url or 'direct input'}\n\n"
        f"---\n{merged}\n---\n\n"
        f"Produce the full markdown note following the system instructions. "
        f"Do not mention that this came from multiple sections — write it as one unified summary."
    )
    raw, provider = await complete(
        system=system,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=2048,
    )
    markdown_part, meta = _parse_output(raw, title)

    return {
        "markdown": markdown_part,
        "frontmatter": meta,
        "entities": meta.get("entities", []),
        "tags": meta.get("tags", []),
        "domain": meta.get("domain", "other"),
        "triples": meta.get("triples", []),
        "provider": provider,
    }
