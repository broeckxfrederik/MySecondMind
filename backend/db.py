import aiosqlite
import json
from datetime import datetime
from backend.config import DB_PATH


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT,
                file_path TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                entities TEXT DEFAULT '[]',
                domain TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                audio_path TEXT,
                summary_version INTEGER DEFAULT 1,
                content_hash TEXT
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL DEFAULT 'mentions',
                weight REAL DEFAULT 1.0,
                UNIQUE(source_id, target_id, relationship)
            );

            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                diff TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                analyzed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS hub_scores (
                note_id TEXT PRIMARY KEY,
                hits_hub_score REAL DEFAULT 0.0,
                hits_auth_score REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notes_domain ON notes(domain);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_edits_note ON edits(note_id);
        """)
        await db.commit()


async def upsert_note(db: aiosqlite.Connection, note: dict):
    tags = json.dumps(note.get("tags", []))
    entities = json.dumps(note.get("entities", []))
    await db.execute("""
        INSERT INTO notes (id, title, source_url, file_path, tags, entities, domain,
                           created_at, updated_at, audio_path, summary_version, content_hash)
        VALUES (:id, :title, :source_url, :file_path, :tags, :entities, :domain,
                :created_at, :updated_at, :audio_path, :summary_version, :content_hash)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            tags=excluded.tags,
            entities=excluded.entities,
            domain=excluded.domain,
            updated_at=excluded.updated_at,
            audio_path=excluded.audio_path,
            summary_version=excluded.summary_version,
            content_hash=excluded.content_hash
    """, {**note, "tags": tags, "entities": entities})
    await db.commit()


async def get_note_by_path(db: aiosqlite.Connection, file_path: str) -> dict | None:
    async with db.execute("SELECT * FROM notes WHERE file_path = ?", (file_path,)) as cur:
        row = await cur.fetchone()
        if row:
            d = dict(row)
            d["tags"] = json.loads(d["tags"])
            d["entities"] = json.loads(d["entities"])
            return d
    return None


async def get_all_notes(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM notes ORDER BY updated_at DESC") as cur:
        rows = await cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d["tags"])
            d["entities"] = json.loads(d["entities"])
            result.append(d)
        return result


async def upsert_edge(db: aiosqlite.Connection, source_id: str, target_id: str,
                      relationship: str, weight: float = 1.0):
    await db.execute("""
        INSERT INTO edges (source_id, target_id, relationship, weight)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id, target_id, relationship) DO UPDATE SET
            weight = weight + 0.1
    """, (source_id, target_id, relationship, weight))
    await db.commit()


async def get_all_edges(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM edges") as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def record_edit(db: aiosqlite.Connection, note_id: str, diff: str):
    await db.execute(
        "INSERT INTO edits (note_id, diff, timestamp, analyzed) VALUES (?, ?, ?, 0)",
        (note_id, diff, datetime.utcnow().isoformat())
    )
    await db.commit()


async def count_unanalyzed_edits(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(*) FROM edits WHERE analyzed = 0") as cur:
        row = await cur.fetchone()
        return row[0] if row else 0


async def get_unanalyzed_edits(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT * FROM edits WHERE analyzed = 0 ORDER BY timestamp ASC"
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def mark_edits_analyzed(db: aiosqlite.Connection, ids: list[int]):
    placeholders = ",".join("?" * len(ids))
    await db.execute(f"UPDATE edits SET analyzed = 1 WHERE id IN ({placeholders})", ids)
    await db.commit()


async def upsert_hub_scores(db: aiosqlite.Connection, scores: list[dict]):
    now = datetime.utcnow().isoformat()
    for s in scores:
        await db.execute("""
            INSERT INTO hub_scores (note_id, hits_hub_score, hits_auth_score, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                hits_hub_score=excluded.hits_hub_score,
                hits_auth_score=excluded.hits_auth_score,
                updated_at=excluded.updated_at
        """, (s["note_id"], s["hub"], s["auth"], now))
    await db.commit()


async def get_hub_scores(db: aiosqlite.Connection) -> dict[str, dict]:
    async with db.execute("SELECT * FROM hub_scores") as cur:
        rows = await cur.fetchall()
        return {r["note_id"]: {"hub": r["hits_hub_score"], "auth": r["hits_auth_score"]} for r in rows}
