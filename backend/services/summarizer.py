import anthropic
from pathlib import Path
from backend.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, USER_PREFS_FILE


def _load_preferences() -> str:
    if USER_PREFS_FILE.exists():
        return USER_PREFS_FILE.read_text(encoding="utf-8")
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


async def summarize(title: str, text: str, source_url: str = "") -> dict:
    """
    Returns:
        {
            "markdown": str,   # full note markdown
            "frontmatter": dict,
            "entities": list[str],
            "tags": list[str],
            "domain": str,
            "triples": list[dict],
        }
    """
    preferences = _load_preferences()
    system = _build_system_prompt(preferences)

    user_message = f"""Please summarize the following content into a structured knowledge note.

Title: {title}
Source: {source_url or "direct input"}

---
{text}
---

Produce the full markdown note following the system instructions."""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text

    # Split markdown from trailing JSON metadata block
    markdown_part = raw
    meta = {
        "title": title,
        "tags": [],
        "entities": [],
        "domain": "other",
        "triples": [],
    }

    if "```json" in raw:
        parts = raw.rsplit("```json", 1)
        markdown_part = parts[0].rstrip("\n -")
        try:
            import json
            json_str = parts[1].split("```")[0].strip()
            parsed = json.loads(json_str)
            meta.update(parsed)
        except Exception:
            pass

    return {
        "markdown": markdown_part,
        "frontmatter": meta,
        "entities": meta.get("entities", []),
        "tags": meta.get("tags", []),
        "domain": meta.get("domain", "other"),
        "triples": meta.get("triples", []),
    }
