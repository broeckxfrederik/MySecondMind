"""
Graph service: builds the knowledge graph from vault notes.

- Extracts [[wikilinks]] from note bodies → edges
- Stores semantic triples (subject → predicate → object) from summarizer
- Runs HITS algorithm to score hub/authority nodes
- Computes Jaccard similarity between notes sharing entities
"""
import re
from pathlib import Path

import aiosqlite
import numpy as np

from backend.config import VAULT_DIR
from backend.db import (
    upsert_edge, get_all_edges, get_all_notes,
    upsert_hub_scores, get_hub_scores
)


# ── Wikilink extraction ────────────────────────────────────────────────────────

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")


def extract_wikilinks(text: str) -> list[str]:
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text)]


# ── Triple validation ──────────────────────────────────────────────────────────

def _is_valid_node(name: str) -> bool:
    """Return False for garbage node names: empty, single char, or pure punctuation."""
    s = name.strip()
    if len(s) <= 2:
        return False
    if re.match(r"^[A-Za-z0-9]$", s):  # single alphanumeric (shouldn't hit after len check, but explicit)
        return False
    return True


def _concept_id(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"\s+", "-", slug).strip("-")
    return f"concept::{slug}"


# ── Triple storage ─────────────────────────────────────────────────────────────

async def store_triples(db: aiosqlite.Connection, source_note_id: str,
                        entities: list[str], triples: list[dict]):
    """Store semantic triples from summarizer output as graph edges."""
    for entity in entities:
        if not _is_valid_node(entity):
            continue
        await upsert_edge(db, source_note_id, _concept_id(entity), "mentions", 1.0)

    for triple in triples:
        subj = triple.get("subject", "").strip()
        pred = triple.get("predicate", "mentions").strip() or "mentions"
        obj = triple.get("object", "").strip()

        if not subj or not obj:
            continue
        if subj.lower() == obj.lower():          # no self-loops
            continue
        if not _is_valid_node(subj) or not _is_valid_node(obj):
            continue

        await upsert_edge(db, _concept_id(subj), _concept_id(obj), pred, 1.0)


# ── Full graph rebuild ─────────────────────────────────────────────────────────

async def rebuild_graph(db: aiosqlite.Connection):
    """
    Scans all vault markdown files, rebuilds edges from [[wikilinks]],
    then runs HITS + Jaccard scoring.
    """
    notes = await get_all_notes(db)
    note_by_title: dict[str, str] = {}  # title (lower) → note_id

    for note in notes:
        note_by_title[note["title"].lower()] = note["id"]

    # Build wikilink edges from file contents
    for note in notes:
        vault_path = VAULT_DIR.parent / note["file_path"]
        if not vault_path.exists():
            continue
        content = vault_path.read_text(encoding="utf-8")
        links = extract_wikilinks(content)
        for link in links:
            target_title = link.lower()
            if target_title in note_by_title:
                target_id = note_by_title[target_title]
            else:
                slug = re.sub(r"[^\w\s-]", "", target_title)
                slug = re.sub(r"\s+", "-", slug).strip("-")
                target_id = f"concept::{slug}"
            await upsert_edge(db, note["id"], target_id, "mentions", 1.0)

    # Jaccard similarity between notes sharing entities
    for i, n1 in enumerate(notes):
        for n2 in notes[i + 1:]:
            s1 = set(n1["entities"])
            s2 = set(n2["entities"])
            if not s1 or not s2:
                continue
            jaccard = len(s1 & s2) / len(s1 | s2)
            if jaccard >= 0.2:  # Only strong similarities
                await upsert_edge(db, n1["id"], n2["id"], "similar_to", jaccard)

    # Run HITS
    await compute_hits(db)


# ── HITS algorithm ─────────────────────────────────────────────────────────────

async def compute_hits(db: aiosqlite.Connection, iterations: int = 50):
    """
    Hyperlink-Induced Topic Search (HITS) algorithm.
    hub_score[n]  = sum of auth_scores of nodes n points to
    auth_score[n] = sum of hub_scores of nodes pointing to n
    """
    edges = await get_all_edges(db)
    if not edges:
        return

    # Build node index
    node_ids = set()
    for e in edges:
        node_ids.add(e["source_id"])
        node_ids.add(e["target_id"])

    node_list = sorted(node_ids)
    idx = {n: i for i, n in enumerate(node_list)}
    n = len(node_list)

    # Adjacency matrix
    adj = np.zeros((n, n), dtype=np.float64)
    for e in edges:
        src = idx.get(e["source_id"])
        tgt = idx.get(e["target_id"])
        if src is not None and tgt is not None:
            adj[src][tgt] += e["weight"]

    hub = np.ones(n)
    auth = np.ones(n)

    for _ in range(iterations):
        new_auth = adj.T @ hub
        new_hub = adj @ auth
        # Normalize
        auth_norm = np.linalg.norm(new_auth)
        hub_norm = np.linalg.norm(new_hub)
        auth = new_auth / auth_norm if auth_norm > 0 else new_auth
        hub = new_hub / hub_norm if hub_norm > 0 else new_hub

    scores = [
        {"note_id": node_list[i], "hub": float(hub[i]), "auth": float(auth[i])}
        for i in range(n)
    ]
    await upsert_hub_scores(db, scores)


# ── Graph response builder ─────────────────────────────────────────────────────

async def build_graph_response(db: aiosqlite.Connection) -> dict:
    """Build the full graph payload for the frontend."""
    notes = await get_all_notes(db)
    edges = await get_all_edges(db)
    hub_scores = await get_hub_scores(db)

    nodes = []
    note_ids = set()

    for note in notes:
        hs = hub_scores.get(note["id"], {"hub": 0.0, "auth": 0.0})
        nodes.append({
            "id": note["id"],
            "title": note["title"],
            "type": "link" if note.get("source_url") else "note",
            "domain": note.get("domain"),
            "hub_score": hs["hub"],
            "auth_score": hs["auth"],
            "tags": note.get("tags", []),
            "file_path": note.get("file_path"),
            "audio_path": note.get("audio_path"),
        })
        note_ids.add(note["id"])

    # Add concept nodes (those not in notes table)
    concept_ids = set()
    for e in edges:
        for nid in [e["source_id"], e["target_id"]]:
            if nid.startswith("concept::") and nid not in concept_ids:
                concept_ids.add(nid)
                label = nid.replace("concept::", "").replace("-", " ").title()
                hs = hub_scores.get(nid, {"hub": 0.0, "auth": 0.0})
                nodes.append({
                    "id": nid,
                    "title": label,
                    "type": "concept",
                    "domain": None,
                    "hub_score": hs["hub"],
                    "auth_score": hs["auth"],
                    "tags": [],
                    "file_path": None,
                    "audio_path": None,
                })

    graph_edges = [
        {
            "source": e["source_id"],
            "target": e["target_id"],
            "label": e["relationship"],
            "weight": e["weight"],
        }
        for e in edges
    ]

    return {"nodes": nodes, "edges": graph_edges}
