# MySecondMind

Personal AI knowledge base — drop a URL or text, get a structured Obsidian note, TTS audio, and a knowledge graph. The system learns from your edits over time.

## Prerequisites

- Docker + Docker Compose on your server
- Traefik running with an external Docker network (`proxy`)
- At least one LLM API key (Anthropic, Groq, or Gemini)

## Setup

**1. Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```env
DOMAIN=mind.yourdomain.com        # your subdomain, must point to this server

ANTHROPIC_API_KEY=sk-ant-...      # at least one of these three
# GROQ_API_KEY=gsk_...
# GEMINI_API_KEY=AIza...
```

Everything else has sensible defaults. See `.env.example` for optional settings (model names, provider priority, TTS voice, etc.).

**2. Build and start**

```bash
docker compose up --build -d
```

The first build downloads the spacy language model (~50 MB) — takes a minute or two.

**3. Verify**

```bash
# Watch startup logs
docker compose logs -f

# Should see: "Application startup complete"
# Then check health from the server itself (bypasses Traefik):
curl http://localhost:8000/health
```

**4. Open in browser**

Navigate to `https://mind.yourdomain.com` — Authentik will gate access automatically via your global Traefik middleware.

## First use

1. Paste a URL into the ingest form and submit
2. Within ~10 seconds you'll have:
   - A markdown note in `vault/links/`
   - An MP3 audio file in `audio/`
   - The note indexed in `data/mysecondmind.db`
3. Click the graph tab to see your growing knowledge network
4. Open `vault/` in Obsidian to browse notes with backlinks and graph view

## Learning loop

Every time you edit a generated note in `vault/`, the diff is recorded. After 10 edits Claude analyzes the patterns and updates `ai-knowledge/user-preferences.md` — future summaries automatically reflect your style.

Trigger consolidation manually: `POST /consolidate-preferences`

## API

| Endpoint | Method | Description |
|---|---|---|
| `/ingest` | POST | Submit `{"url": "..."}` or `{"text": "...", "title": "..."}` |
| `/notes` | GET | List all notes |
| `/notes/{id}` | GET | Get note with full content |
| `/graph` | GET | Graph data for vis.js |
| `/audio/{id}` | GET | Stream MP3 for a note |
| `/rebuild-graph` | POST | Recompute all graph edges + HITS scores |
| `/consolidate-preferences` | POST | Manually trigger preference consolidation |
| `/health` | GET | Server + LLM provider status |

## Updating

```bash
git pull
docker compose up --build -d
```

Data lives in Docker volumes (`vault/`, `audio/`, `ai-knowledge/`, `data/`) and is never touched by a rebuild.
