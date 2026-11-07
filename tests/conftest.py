"""Shared fixtures. Tests never call a real LLM and use a tiny fake embedder,
so they run offline, fast and without an API key."""

import hashlib
import math
import os
import re
import tempfile

# Settings must be in the environment before the app modules are imported.
# (load_dotenv never overrides variables that already exist.)
_TMP = tempfile.mkdtemp(prefix="ragtest-")
os.environ.update({
    "BASE_URL": "http://llm.test/v1",
    "API_KEY": "test-key",
    "MODEL_NAME": "test-model",
    "ADMIN_TOKEN": "secret-token",
    "DATA_DIR": _TMP,
    "WARMUP": "0",
    "MIN_SCORE": "0.2",
    "PER_IP_PER_MINUTE": "1000",
    "PER_IP_PER_DAY": "1000",
    "DAILY_CAP": "10000",
    "ALLOWED_ORIGINS": "https://figureshed.com,http://localhost:8000",
    "BOT_NAME": "Test Bot",
    "CONTACT_URL": "https://example.com/contact",
    "SUGGESTIONS": "What is horsepower?|How does it work?",
})

import pytest  # noqa: E402

from app import main, rag  # noqa: E402
from app.vectorstore import VectorStore  # noqa: E402

_WORD = re.compile(r"[a-z0-9]+")
_DIM = 512


class FakeEmbedder:
    """Hashed bag-of-words vectors: texts sharing words get high cosine similarity."""

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for word in _WORD.findall(text.lower()):
            if len(word) > 2:
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % _DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = VectorStore(str(tmp_path / "db"), "test", FakeEmbedder())
    monkeypatch.setattr(rag, "get_store", lambda: s)
    monkeypatch.setattr(main, "get_store", lambda: s)
    return s


@pytest.fixture(autouse=True)
def fresh_limiter():
    main.get_limiter.cache_clear()
    yield
    main.get_limiter.cache_clear()
