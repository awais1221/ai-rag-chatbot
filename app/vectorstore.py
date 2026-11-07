"""Chroma vector store + local fastembed embeddings.

Embeddings are computed here (not by Chroma) with ``fastembed``, a small ONNX
runtime model that runs on CPU: free, private and with no rate limits. The
embedder is injectable so tests can use a tiny fake one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import PROJECT_ROOT, get_settings
from app.schemas import Chunk, SearchHit, SourceInfo

logger = logging.getLogger(__name__)

_BATCH = 64


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """Wraps fastembed. The model (~130 MB) downloads once on first use."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        cache_dir = os.getenv("FASTEMBED_CACHE_PATH", str(PROJECT_ROOT / ".cache" / "fastembed"))
        logger.info("Loading embedding model %s", model_name)
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts, batch_size=_BATCH)]

    def embed_query(self, text: str) -> list[float]:
        # query_embed adds the model's recommended "search query" instruction
        return next(iter(self._model.query_embed(text))).tolist()


class VectorStore:
    def __init__(self, path: str, collection: str, embedder: Embedder):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._name = collection
        self._embedder = embedder
        self._lock = threading.Lock()
        self._collection = self._open()

    def _open(self):
        return self._client.get_or_create_collection(
            name=self._name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=None,  # we always pass our own embeddings
        )

    # ------------------------------------------------------------ write
    def add_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        embeddings = self._embedder.embed_documents([c.text for c in chunks])
        ids, metadatas = [], []
        for c in chunks:
            key = f"{c.source}|{c.page or 0}|{c.chunk_index}"
            ids.append(hashlib.sha1(key.encode("utf-8")).hexdigest())
            meta = {"source": c.source, "title": c.title, "source_type": c.source_type,
                    "chunk_index": c.chunk_index, "heading": c.heading}
            if c.page is not None:
                meta["page"] = c.page
            metadatas.append(meta)
        with self._lock:
            for start in range(0, len(ids), _BATCH * 4):
                end = start + _BATCH * 4
                self._collection.upsert(
                    ids=ids[start:end],
                    embeddings=embeddings[start:end],
                    documents=[c.text for c in chunks[start:end]],
                    metadatas=metadatas[start:end],
                )
        return len(ids)

    def replace_source(self, source: str, chunks: list[Chunk]) -> int:
        """Re-indexing a file/page replaces its old chunks instead of duplicating."""
        self.delete_source(source)
        return self.add_chunks(chunks)

    def delete_source(self, source: str) -> int:
        with self._lock:
            existing = self._collection.get(where={"source": source}, include=[])
            if existing["ids"]:
                self._collection.delete(ids=existing["ids"])
            return len(existing["ids"])

    def reset(self) -> None:
        with self._lock:
            self._client.delete_collection(self._name)
            self._collection = self._open()

    # ------------------------------------------------------------ read
    def count(self) -> int:
        return self._collection.count()

    def search(self, query: str, k: int) -> list[SearchHit]:
        if self.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=[self._embedder.embed_query(query)],
            n_results=min(k, self.count()),
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for text, meta, dist in zip(result["documents"][0], result["metadatas"][0],
                                    result["distances"][0]):
            hits.append(SearchHit(
                text=text,
                source=meta.get("source", ""),
                title=meta.get("title", ""),
                source_type=meta.get("source_type", "text"),
                page=meta.get("page"),
                heading=meta.get("heading", ""),
                score=round(1.0 - float(dist), 4),  # cosine distance -> similarity
            ))
        return hits

    def list_sources(self) -> list[SourceInfo]:
        data = self._collection.get(include=["metadatas"])
        grouped: dict[str, SourceInfo] = {}
        for meta in data["metadatas"]:
            src = meta.get("source", "")
            if src not in grouped:
                title = meta.get("title", src)
                if meta.get("source_type") == "pdf":
                    title = src  # per-page titles -> show the file name once
                grouped[src] = SourceInfo(source=src, title=title,
                                          source_type=meta.get("source_type", "text"),
                                          chunks=0)
            grouped[src].chunks += 1
        return sorted(grouped.values(), key=lambda s: s.title.lower())


@lru_cache
def get_store() -> VectorStore:
    settings = get_settings()
    return VectorStore(settings.data_dir, settings.collection_name,
                       FastEmbedder(settings.embedding_model))
