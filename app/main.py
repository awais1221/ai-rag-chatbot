"""FastAPI app: chat API (+ streaming), knowledge-base admin API, web UI and widget."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import rag
from app.chunker import chunk_documents
from app.config import PROJECT_ROOT, get_settings
from app.llm_client import LLMError, LLMNotConfiguredError
from app.loaders import LoaderError, WebLoader, load_file
from app.logging_config import setup_logging
from app.ratelimit import RateLimiter, RateLimitExceeded
from app.schemas import (
    ChatRequest,
    ChatResponse,
    Document,
    IngestResponse,
    IngestResult,
    IngestUrlRequest,
    SourceInfo,
)
from app.vectorstore import get_store

logger = logging.getLogger(__name__)
STATIC_DIR = PROJECT_ROOT / "static"
MAX_FILES_PER_UPLOAD = 20


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = get_settings()
    if not settings.llm_configured:
        logger.warning("LLM not configured: chat will return a setup message. See .env.example")
    if not settings.admin_enabled:
        logger.warning("ADMIN_TOKEN not set: uploading / editing the knowledge base is disabled")
    if os.getenv("WARMUP", "1") != "0":
        # Load the embedding model in the background so the first question is fast,
        # and re-index SEED_URLS if the knowledge base is empty (free hosts wipe disk).
        threading.Thread(target=_warmup, daemon=True, name="warmup").start()
    yield


def _warmup() -> None:
    try:
        store = get_store()
        seeds = [u.strip() for u in os.getenv("SEED_URLS", "").split(",") if u.strip()]
        if store.count() == 0 and seeds:
            loader = WebLoader()
            for url in seeds:
                docs, errors = loader.load_url(url)
                counts = _index(docs)
                logger.info("Seeded %d page(s) / %d chunks from %s (%d failed)",
                            len(docs), sum(counts.values()), url, len(errors))
    except Exception:
        logger.exception("Warm-up / seeding failed")


app = FastAPI(
    title="AI RAG Chatbot",
    description="Chat with your documents and website. Answers cite their sources.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().allowed_origins),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token"],
    max_age=600,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ====================================================================== helpers
@lru_cache
def get_limiter() -> RateLimiter:
    s = get_settings()
    return RateLimiter(s.per_ip_per_minute, s.per_ip_per_day, s.daily_cap)


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # The right-most entry is the one added by our own proxy (not spoofable).
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.admin_enabled:
        raise HTTPException(403, "Admin features are disabled. Set ADMIN_TOKEN in .env to enable them.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(401, "Invalid or missing admin token.")


def _check_chat(req: ChatRequest, request: Request) -> str:
    settings = get_settings()
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    if len(question) > settings.max_question_chars:
        raise HTTPException(400, f"Please keep questions under {settings.max_question_chars} characters.")
    try:
        get_limiter().check(client_ip(request))
    except RateLimitExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    return question


def _index(docs: list[Document]) -> dict[str, int]:
    """Chunk + embed documents, grouped by source. Returns {source: chunk_count}."""
    settings = get_settings()
    store = get_store()
    by_source: dict[str, list[Document]] = {}
    for doc in docs:
        by_source.setdefault(doc.source, []).append(doc)
    counts = {}
    for source, group in by_source.items():
        chunks = chunk_documents(group, settings.chunk_size, settings.chunk_overlap)
        counts[source] = store.replace_source(source, chunks)
    return counts


# ====================================================================== pages
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/widget.js", include_in_schema=False)
def widget() -> FileResponse:
    return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "llm_configured": settings.llm_configured,
            "model": settings.model_name or None}


@app.get("/config")
def public_config() -> dict:
    """Branding + feature flags for the UI and widget (no secrets)."""
    settings = get_settings()
    return {
        "bot_name": settings.bot_name,
        "suggestions": list(settings.suggestions),
        "admin_enabled": settings.admin_enabled,
        "llm_configured": settings.llm_configured,
        "max_question_chars": settings.max_question_chars,
    }


# ====================================================================== chat
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    question = _check_chat(req, request)
    try:
        return rag.answer(question, req.history)
    except LLMNotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Server-Sent Events: sources, then tokens, then done (or an error event)."""
    question = _check_chat(req, request)

    def events():
        try:
            for event in rag.stream_answer(question, req.history):
                yield f"data: {json.dumps(event)}\n\n"
        except (LLMNotConfiguredError, LLMError) as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        except Exception:  # never leak a stack trace to visitors
            logger.exception("Unexpected error while streaming an answer")
            msg = "Something went wrong on our side. Please try again."
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ====================================================================== admin
@app.post("/ingest/files", response_model=IngestResponse, dependencies=[Depends(require_admin)])
def ingest_files(files: list[UploadFile] = File(...)) -> IngestResponse:
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"Upload at most {MAX_FILES_PER_UPLOAD} files at a time.")
    results = []
    for upload in files:
        name = os.path.basename(upload.filename or "file")
        try:
            docs = load_file(name, upload.file.read())
            chunks = _index(docs).get(name, 0)
            results.append(IngestResult(source=name, title=name, status="success", chunks=chunks))
            logger.info("Indexed file %s (%d chunks)", name, chunks)
        except LoaderError as exc:
            results.append(IngestResult(source=name, title=name, status="error", error=str(exc)))
        except Exception:
            logger.exception("Failed to index %s", name)
            results.append(IngestResult(source=name, title=name, status="error",
                                        error="Unexpected error while indexing this file."))
    return IngestResponse(results=results, total_chunks=sum(r.chunks for r in results))


@app.post("/ingest/url", response_model=IngestResponse, dependencies=[Depends(require_admin)])
def ingest_url(req: IngestUrlRequest) -> IngestResponse:
    try:
        docs, errors = WebLoader().load_url(req.url.strip(), req.mode, req.max_pages)
    except LoaderError as exc:
        raise HTTPException(400, str(exc)) from exc
    counts = _index(docs)
    results = [IngestResult(source=d.source, title=d.title, status="success",
                            chunks=counts.get(d.source, 0)) for d in docs]
    results += [IngestResult(source=url, title=url, status="error", error=err)
                for url, err in errors]
    logger.info("Indexed %d page(s) from %s (%d failed)", len(docs), req.url, len(errors))
    return IngestResponse(results=results, total_chunks=sum(counts.values()))


@app.get("/sources", response_model=list[SourceInfo], dependencies=[Depends(require_admin)])
def list_sources() -> list[SourceInfo]:
    return get_store().list_sources()


@app.delete("/sources", dependencies=[Depends(require_admin)])
def delete_source(source: str) -> dict:
    removed = get_store().delete_source(source)
    if not removed:
        raise HTTPException(404, "Source not found.")
    return {"deleted": source, "chunks": removed}


@app.post("/reset", dependencies=[Depends(require_admin)])
def reset() -> dict:
    get_store().reset()
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})
