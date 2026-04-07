import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import BASE_DIR, AUDIO_DIR
from backend.db import init_db, get_db
from backend.models import IngestRequest
from backend.services.ingest import ingest
from backend.services.graph import rebuild_graph, build_graph_response
from backend.services.learner import consolidate_preferences
from backend.services.watcher import VaultWatcher
from backend.services.validator import validation_loop, record_activity
from backend.services.llm import provider_status


_watcher: VaultWatcher | None = None
_validator_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watcher, _validator_task
    await init_db()

    # Start vault file watcher (thread-based)
    loop = asyncio.get_event_loop()
    _watcher = VaultWatcher(loop)
    _watcher.start()

    # Start idle cross-validator (async background task)
    _validator_task = asyncio.create_task(validation_loop())

    yield

    if _watcher:
        _watcher.stop()
    if _validator_task:
        _validator_task.cancel()


app = FastAPI(
    title="MySecondMind",
    description="Personal knowledge management — second mind for human and AI",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request, exc):
    msg = str(exc)
    if "All LLM providers" in msg:
        return JSONResponse(status_code=503, content={"detail": "All LLM providers are unavailable or rate-limited. Try again in a minute."})
    return JSONResponse(status_code=500, content={"detail": msg})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API routes ─────────────────────────────────────────────────────────────────

@app.post("/ingest")
async def ingest_content(request: IngestRequest, background_tasks: BackgroundTasks):
    """
    Ingest a URL or raw text. Runs the full pipeline:
    scrape → summarize → TTS → save to vault + DB.
    """
    if not request.url and not request.text:
        raise HTTPException(status_code=400, detail="Provide either 'url' or 'text'")

    record_activity()

    async with get_db() as db:
        note = await ingest(
            db,
            url=request.url,
            text=request.text,
            title=request.title,
        )

    background_tasks.add_task(_rebuild_graph_bg)
    return {"status": "ok", "note": note}


@app.get("/notes")
async def list_notes():
    """Return all notes whose files still exist on disk, ordered by most recently updated."""
    from backend.db import get_all_notes
    async with get_db() as db:
        notes = await get_all_notes(db)
    return [n for n in notes if (BASE_DIR / n["file_path"]).exists()]


@app.get("/notes/{note_id}")
async def get_note(note_id: str):
    """Get a single note by ID, including its file content."""
    from backend.db import get_all_notes
    async with get_db() as db:
        notes = await get_all_notes(db)
    for n in notes:
        if n["id"] == note_id:
            fp = BASE_DIR / n["file_path"]
            if fp.exists():
                n["content"] = fp.read_text(encoding="utf-8")
            return n
    raise HTTPException(status_code=404, detail="Note not found")


@app.get("/graph")
async def get_graph():
    """Return graph data for the frontend vis.js visualization."""
    async with get_db() as db:
        graph = await build_graph_response(db)
    return graph


@app.get("/audio/{note_id}")
async def get_audio(note_id: str):
    """Stream the TTS audio file for a note."""
    audio_file = AUDIO_DIR / f"{note_id}.mp3"
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(audio_file, media_type="audio/mpeg")


@app.post("/rebuild-graph")
async def trigger_rebuild():
    """Manually trigger a full graph rebuild (wikilink extraction + HITS)."""
    record_activity()
    async with get_db() as db:
        await rebuild_graph(db)
    return {"status": "ok", "message": "Graph rebuilt"}


@app.post("/consolidate-preferences")
async def trigger_consolidation():
    """Manually trigger preference consolidation from edit log."""
    record_activity()
    async with get_db() as db:
        await consolidate_preferences(db)
    return {"status": "ok", "message": "Preferences consolidated"}


@app.get("/health")
async def health():
    """Health check including LLM provider availability."""
    return {
        "status": "ok",
        "providers": provider_status(),
    }


# ── Background helpers ─────────────────────────────────────────────────────────

async def _rebuild_graph_bg():
    async with get_db() as db:
        await rebuild_graph(db)


# ── Static files (frontend) ────────────────────────────────────────────────────
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
