"""Data models shared across the app (internal dataclasses + API schemas)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- internal
@dataclass
class Document:
    """A loaded piece of source material (a web page, a PDF page, a file)."""
    text: str
    source: str                       # URL or filename, used to group/delete
    title: str                        # human-readable label for citations
    source_type: Literal["url", "pdf", "docx", "text"] = "text"
    page: int | None = None           # PDF page number (1-based)


@dataclass
class Chunk:
    text: str
    source: str
    title: str
    source_type: str
    chunk_index: int
    page: int | None = None
    heading: str = ""


@dataclass
class SearchHit:
    text: str
    source: str
    title: str
    source_type: str
    score: float                      # cosine similarity, 0..1 (higher = closer)
    page: int | None = None
    heading: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- API
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class Source(BaseModel):
    index: int                        # the [n] number used in the answer
    title: str
    source: str
    url: str | None = None            # set for web pages
    page: int | None = None
    snippet: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    found: bool                       # False = nothing relevant in the knowledge base


class IngestResult(BaseModel):
    source: str
    title: str
    status: Literal["success", "error"]
    chunks: int = 0
    error: str | None = None


class IngestResponse(BaseModel):
    results: list[IngestResult]
    total_chunks: int


class IngestUrlRequest(BaseModel):
    url: str = Field(min_length=4, max_length=2000)
    mode: Literal["auto", "page", "sitemap", "crawl"] = "auto"
    max_pages: int | None = Field(default=None, ge=1, le=500)


class SourceInfo(BaseModel):
    source: str
    title: str
    source_type: str
    chunks: int
