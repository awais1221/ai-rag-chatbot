"""Application settings loaded from environment variables / a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root (does nothing if the file does not exist).
load_dotenv(PROJECT_ROOT / ".env")

_PLACEHOLDER_MARKERS = ("your-", "your_", "xxx", "changeme")


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _get_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


def _is_placeholder(value: str) -> bool:
    value = value.strip().lower()
    return not value or any(m in value for m in _PLACEHOLDER_MARKERS)


@dataclass(frozen=True)
class Settings:
    # LLM (any OpenAI-compatible provider)
    base_url: str
    api_key: str
    model_name: str
    llm_timeout: int = 60
    llm_temperature: float = 0.2
    rate_limit_retries: int = 2
    rate_limit_max_wait: int = 65

    # Retrieval
    data_dir: str = str(PROJECT_ROOT / "data")
    collection_name: str = "knowledge_base"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 4
    min_score: float = 0.55        # cosine similarity below this = "not in the docs"
    history_turns: int = 3

    # Ingestion
    max_file_mb: int = 15
    max_crawl_pages: int = 50

    # Security / abuse protection
    admin_token: str = ""
    allowed_origins: tuple[str, ...] = ()
    max_question_chars: int = 500
    per_ip_per_minute: int = 8
    per_ip_per_day: int = 60
    daily_cap: int = 500
    trust_proxy: bool = False      # behind HF Spaces / a reverse proxy: read X-Forwarded-For

    # Branding (shown in UI / widget, used in the "not found" reply)
    bot_name: str = "Site Assistant"
    contact_url: str = ""
    suggestions: tuple[str, ...] = ()  # starter questions shown in the chat

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def llm_configured(self) -> bool:
        """True when enough settings exist to call an LLM."""
        return (not _is_placeholder(self.api_key)) and bool(self.base_url) and bool(self.model_name)

    @property
    def admin_enabled(self) -> bool:
        return not _is_placeholder(self.admin_token)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        base_url=os.getenv("BASE_URL", "").strip(),
        api_key=os.getenv("API_KEY", "").strip(),
        model_name=os.getenv("MODEL_NAME", "").strip(),
        llm_timeout=_get_int("LLM_TIMEOUT", 60),
        llm_temperature=_get_float("LLM_TEMPERATURE", 0.2),
        rate_limit_retries=max(0, _get_int("RATE_LIMIT_RETRIES", 2)),
        rate_limit_max_wait=_get_int("RATE_LIMIT_MAX_WAIT", 65),
        data_dir=os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")),
        collection_name=os.getenv("COLLECTION_NAME", "knowledge_base"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        chunk_size=max(200, _get_int("CHUNK_SIZE", 800)),
        chunk_overlap=max(0, _get_int("CHUNK_OVERLAP", 120)),
        top_k=max(1, _get_int("TOP_K", 4)),
        min_score=_get_float("MIN_SCORE", 0.55),
        history_turns=max(0, _get_int("HISTORY_TURNS", 3)),
        max_file_mb=_get_int("MAX_FILE_MB", 15),
        max_crawl_pages=max(1, _get_int("MAX_CRAWL_PAGES", 50)),
        admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        allowed_origins=_get_list(
            "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
        ),
        max_question_chars=_get_int("MAX_QUESTION_CHARS", 500),
        per_ip_per_minute=_get_int("PER_IP_PER_MINUTE", 8),
        per_ip_per_day=_get_int("PER_IP_PER_DAY", 60),
        daily_cap=_get_int("DAILY_CAP", 500),
        trust_proxy=os.getenv("TRUST_PROXY", "false").strip().lower() in ("1", "true", "yes"),
        bot_name=os.getenv("BOT_NAME", "Site Assistant").strip() or "Site Assistant",
        contact_url=os.getenv("CONTACT_URL", "").strip(),
        suggestions=tuple(q.strip() for q in os.getenv("SUGGESTIONS", "").split("|") if q.strip()),
    )
