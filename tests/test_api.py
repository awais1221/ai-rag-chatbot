import json

import pytest
from fastapi.testclient import TestClient

from app import llm_client, main
from app.chunker import chunk_documents
from app.llm_client import LLMError, LLMNotConfiguredError
from app.schemas import Document

ADMIN = {"X-Admin-Token": "secret-token"}


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda m: "Torque times rpm over 5252 [1].")
    monkeypatch.setattr(llm_client, "stream_llm", lambda m: iter(["Torque ", "x rpm [1]."]))
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def kb(store):
    store.add_chunks(chunk_documents([Document(
        text="Horsepower equals torque times rpm divided by 5252.",
        source="https://s.test/hp", title="HP", source_type="url")]))


def test_health_and_config(client):
    assert client.get("/health").json()["llm_configured"] is True
    cfg = client.get("/config").json()
    assert cfg["bot_name"] == "Test Bot"
    assert cfg["suggestions"] == ["What is horsepower?", "How does it work?"]
    assert "secret-token" not in json.dumps(cfg) and "test-key" not in json.dumps(cfg)


def test_pages_are_served(client):
    assert client.get("/").status_code == 200
    resp = client.get("/widget.js")
    assert resp.status_code == 200 and "javascript" in resp.headers["content-type"]


def test_chat_answers_with_sources(client, kb):
    data = client.post("/chat", json={"question": "horsepower torque rpm"}).json()
    assert data["found"] and data["sources"][0]["url"] == "https://s.test/hp"


@pytest.mark.parametrize("question,status", [("   ", 400), ("x" * 501, 400), ("", 422)])
def test_chat_validates_question(client, question, status):
    assert client.post("/chat", json={"question": question}).status_code == status


def test_chat_rate_limited(client, kb, monkeypatch):
    from app.ratelimit import RateLimiter

    monkeypatch.setattr(main, "get_limiter", lambda: limiter)
    limiter = RateLimiter(per_minute=2, per_day=100, daily_cap=100)
    for _ in range(2):
        assert client.post("/chat", json={"question": "horsepower"}).status_code == 200
    resp = client.post("/chat", json={"question": "horsepower"})
    assert resp.status_code == 429 and "too quickly" in resp.json()["detail"]
    assert int(resp.headers["retry-after"]) > 0


@pytest.mark.parametrize("exc,status", [(LLMNotConfiguredError("not configured"), 503),
                                        (LLMError("quota used up"), 502)])
def test_chat_llm_errors(client, kb, monkeypatch, exc, status):
    def boom(_):
        raise exc
    monkeypatch.setattr(llm_client, "call_llm", boom)
    resp = client.post("/chat", json={"question": "horsepower torque"})
    assert resp.status_code == status and str(exc) in resp.json()["detail"]


def _sse(resp):
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_chat_stream(client, kb):
    resp = client.post("/chat/stream", json={"question": "horsepower torque rpm"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse(resp)
    assert [e["type"] for e in events] == ["sources", "token", "token", "done"]
    assert events[-1]["cited"] == [1]


def test_chat_stream_error_event(client, kb, monkeypatch):
    def boom(_):
        raise LLMError("The free AI quota for today is used up")
        yield  # pragma: no cover
    monkeypatch.setattr(llm_client, "stream_llm", boom)
    events = _sse(client.post("/chat/stream", json={"question": "horsepower torque"}))
    assert events[-1] == {"type": "error", "message": "The free AI quota for today is used up"}


def test_admin_endpoints_need_token(client):
    assert client.get("/sources").status_code == 401
    assert client.get("/sources", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/reset").status_code == 401
    assert client.post("/ingest/url", json={"url": "https://s.test/"}).status_code == 401


def test_ingest_files_list_delete_reset(client):
    files = [("files", ("faq.txt", b"Shipping is free on orders over fifty dollars.", "text/plain")),
             ("files", ("virus.exe", b"MZ", "application/octet-stream"))]
    data = client.post("/ingest/files", files=files, headers=ADMIN).json()
    assert [r["status"] for r in data["results"]] == ["success", "error"]
    assert data["total_chunks"] == 1 and "Unsupported" in data["results"][1]["error"]

    sources = client.get("/sources", headers=ADMIN).json()
    assert sources == [{"source": "faq.txt", "title": "faq.txt", "source_type": "text", "chunks": 1}]

    assert client.request("DELETE", "/sources", params={"source": "faq.txt"}, headers=ADMIN).status_code == 200
    assert client.request("DELETE", "/sources", params={"source": "faq.txt"}, headers=ADMIN).status_code == 404
    assert client.post("/reset", headers=ADMIN).json() == {"status": "ok"}


def test_ingest_url_uses_loader(client, monkeypatch):
    class FakeLoader:
        def load_url(self, url, mode, max_pages):
            return ([Document(text="Quarter mile ET depends on power to weight.",
                              source=url, title="Quarter mile", source_type="url")],
                    [("https://s.test/broken", "returned HTTP 404.")])
    monkeypatch.setattr(main, "WebLoader", FakeLoader)
    data = client.post("/ingest/url", json={"url": "https://s.test/qm"}, headers=ADMIN).json()
    assert [r["status"] for r in data["results"]] == ["success", "error"]
    assert data["total_chunks"] == 1


def test_ingest_url_bad_url(client):
    resp = client.post("/ingest/url", json={"url": "notaurl"}, headers=ADMIN)
    assert resp.status_code == 400 and "http" in resp.json()["detail"]


def test_cors_only_for_allowed_origins(client):
    ok = client.options("/chat", headers={"Origin": "https://figureshed.com",
                                          "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://figureshed.com"
    bad = client.options("/chat", headers={"Origin": "https://evil.test",
                                           "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
