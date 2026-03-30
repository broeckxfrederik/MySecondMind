from pydantic import BaseModel
from typing import Optional


class IngestRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None


class NoteResponse(BaseModel):
    id: str
    title: str
    source_url: Optional[str]
    file_path: str
    tags: list[str]
    entities: list[str]
    domain: Optional[str]
    created_at: str
    updated_at: str
    audio_path: Optional[str]
    summary_version: int


class GraphNode(BaseModel):
    id: str
    title: str
    type: str  # "link", "note", "concept"
    domain: Optional[str]
    hub_score: float = 0.0
    auth_score: float = 0.0
    tags: list[str] = []


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    weight: float


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
