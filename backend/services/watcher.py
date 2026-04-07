"""
File watcher: monitors vault/ for edits and triggers the learning loop.
Runs as a background thread started with the FastAPI app lifespan.
"""
import asyncio
import hashlib
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
from watchdog.observers import Observer

from backend.config import VAULT_DIR


class VaultEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._debounce: dict[str, float] = {}
        self._debounce_sec = 3.0  # Wait 3s after last change before processing

    def on_modified(self, event):
        if event.is_directory:
            return
        if not str(event.src_path).endswith(".md"):
            return
        self._schedule(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        if not str(event.src_path).endswith(".md"):
            return
        self._schedule(event.src_path)

    def _schedule(self, path: str):
        self._debounce[path] = time.monotonic()
        asyncio.run_coroutine_threadsafe(
            self._debounced_handle(path), self._loop
        )

    async def _debounced_handle(self, path: str):
        await asyncio.sleep(self._debounce_sec)
        # Only process if no newer event came in
        if time.monotonic() - self._debounce.get(path, 0) < self._debounce_sec - 0.1:
            return
        await _handle_vault_change(path)


async def _handle_vault_change(file_path: str):
    """Called when a vault .md file changes. Records diff and triggers learning."""
    try:
        from backend.db import get_db, get_note_by_path, record_edit, count_unanalyzed_edits
        from backend.services.learner import analyze_edits, consolidate_preferences
        from backend.config import VAULT_DIR, EDITS_BEFORE_CONSOLIDATION
        import difflib

        path = Path(file_path)
        if not path.exists():
            return

        new_content = path.read_text(encoding="utf-8")
        new_hash = hashlib.md5(new_content.encode()).hexdigest()

        # Make path relative to vault parent (project root)
        try:
            rel_path = str(path.relative_to(VAULT_DIR.parent))
        except ValueError:
            rel_path = file_path

        async with get_db() as db:
            note = await get_note_by_path(db, rel_path)
            if not note:
                return  # Not a tracked note

            if note.get("content_hash") == new_hash:
                return  # No actual change

            # Retrieve old content from vault (we stored hash but not content)
            # We diff against the stored summary version as best-effort
            # For now, record the new content as the diff context
            old_placeholder = f"[previous version — hash: {note.get('content_hash', 'unknown')}]"
            diff = "\n".join(
                difflib.unified_diff(
                    old_placeholder.splitlines(),
                    new_content.splitlines(),
                    fromfile="before",
                    tofile="after",
                    lineterm="",
                )
            )

            await record_edit(db, note["id"], diff if diff.strip() else new_content[:500])

            # Update hash in DB
            from backend.db import upsert_note
            note["content_hash"] = new_hash
            note["summary_version"] = note.get("summary_version", 1) + 1
            from datetime import datetime
            note["updated_at"] = datetime.utcnow().isoformat()
            await upsert_note(db, note)

            # Trigger learning if threshold reached
            unanalyzed = await count_unanalyzed_edits(db)
            if unanalyzed >= EDITS_BEFORE_CONSOLIDATION:
                await consolidate_preferences(db)
            else:
                await analyze_edits(db)

    except Exception as e:
        print(f"[watcher] Error handling change for {file_path}: {e}")


class VaultWatcher:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._observer = Observer()
        self._handler = VaultEventHandler(loop)

    def start(self):
        self._observer.schedule(self._handler, str(VAULT_DIR), recursive=True)
        self._observer.start()
        print(f"[watcher] Watching {VAULT_DIR}")

    def stop(self):
        self._observer.stop()
        self._observer.join()
        print("[watcher] Stopped")
