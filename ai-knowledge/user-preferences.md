# User Summary Preferences

> This file is read by the AI before generating any summary or structured note.
> It is updated automatically as the user edits summaries.

## Summary Style

- **Length**: Medium — 3–5 paragraphs. Not a wall of text, not a single line.
- **Structure**: Always use headings. Start with a "## TL;DR" (2–3 sentences), then "## Key Ideas", then "## Connections" (how this links to other concepts).
- **Tone**: Direct, no filler phrases like "It's important to note that...". Get to the point.
- **Wikilinks**: Always extract 3–8 key entities/concepts as `[[wikilinks]]` inline in the text. These drive the knowledge graph.
- **Frontmatter**: Always include YAML frontmatter with `title`, `source`, `created`, `tags`, `entities`, `domain`, `audio`.

## Frontmatter Template

```yaml
---
title: "Note Title"
source: "https://..."
created: "YYYY-MM-DD"
tags: [tag1, tag2]
entities: [Entity1, Entity2, Entity3]
domain: "technology"  # one of: technology, science, business, philosophy, creativity, health, other
audio: "audio/filename.mp3"
---
```

## Preferred Domains

- Technology (software, AI, systems)
- Knowledge management and learning
- OSINT and research techniques

## Wikilink Rules

- Always link to concepts, not just names
- Prefer linking to ideas over people unless the person IS the concept
- Format: `[[Concept Name]]` — title case, no underscores
