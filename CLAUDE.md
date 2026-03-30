# MySecondMind — AI Agent Knowledge Base

This is the personal knowledge management system for this user.
Before generating **any** content (summaries, notes, analyses), read the files below.

## Required Reading Before Output

1. **`ai-knowledge/user-preferences.md`** — How the user wants summaries formatted, what style they prefer, frontmatter template, wikilink rules.
2. **`ai-knowledge/domain-interests.md`** — What topics this user cares about. Use this to weight which entities to extract and link.
3. **`ai-knowledge/learned-patterns.md`** — Recent observations about how the user edits AI-generated content. Apply these corrections proactively.

## System Architecture

- **`vault/`** — Obsidian-compatible markdown vault. All notes live here.
  - `vault/links/` — Auto-summaries of URLs
  - `vault/notes/` — Personal notes and raw dumps
  - `vault/concepts/` — Auto-generated concept stub pages (one page per `[[wikilink]]` entity)
  - `vault/canvas/` — Visual flow canvases for hub concepts
- **`audio/`** — TTS audio files (.mp3), one per note
- **`ai-knowledge/`** — This directory. Do not delete or restructure.
- **`data/`** — SQLite database (do not edit manually)
- **`backend/`** — FastAPI server code
- **`frontend/`** — Web UI

## Development Branch

Always develop on: `claude/knowledge-base-system-RlEhK`

## Key Conventions

- All vault notes use YAML frontmatter (see `ai-knowledge/user-preferences.md` for template)
- Entity wikilinks use `[[Title Case]]` format
- Concept stub pages are minimal: just a title, aliases, and a "## Mentioned In" backlinks section
- Graph edges use semantic predicates: "founded", "created", "mentions", "builds_on", "contradicts", "similar_to"
- SQLite DB at `data/mysecondmind.db` tracks metadata and graph relationships

## API Endpoints (FastAPI, port 8000)

- `POST /ingest` — Submit a URL or raw text for processing
- `GET /notes` — List all notes
- `GET /graph` — Graph data for vis.js frontend
- `GET /audio/{note_id}` — Stream audio for a note
- `POST /rebuild-graph` — Recompute all graph edges + HITS scores
- `POST /consolidate-preferences` — Trigger manual preference consolidation from edit log

## Learning Loop

The system watches `vault/` for file changes. When you (the user) edit a generated summary:
1. The diff is recorded in `data/mysecondmind.db`
2. Claude analyzes what changed and why
3. Insights are appended to `ai-knowledge/learned-patterns.md`
4. After 10 edits, patterns are consolidated into `ai-knowledge/user-preferences.md`

**This means every edit you make teaches the system to write better next time.**

## Session Start Checklist

When starting a new session on this codebase:
1. Read `ai-knowledge/user-preferences.md`
2. Read `ai-knowledge/learned-patterns.md` (last 20 entries)
3. Read `ai-knowledge/session-notes.md` (last session summary)
4. Check `data/mysecondmind.db` for recent activity if needed
