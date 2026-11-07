import pytest

from app import llm_client, rag
from app.chunker import chunk_documents
from app.schemas import ChatTurn, Document

DOCS = [
    Document(text="# Horsepower\n\nHorsepower equals torque times rpm divided by 5252.",
             source="https://s.test/hp", title="Horsepower calculator", source_type="url"),
    Document(text="# Compression\n\nCompression ratio compares swept volume plus chamber volume.",
             source="https://s.test/cr", title="Compression calculator", source_type="url"),
]


@pytest.fixture
def kb(store):
    store.add_chunks(chunk_documents(DOCS))
    return store


@pytest.fixture
def llm(monkeypatch):
    calls = []

    def fake_call(messages):
        calls.append(messages)
        return "Horsepower is torque x rpm / 5252 [1]."

    def fake_stream(messages):
        calls.append(messages)
        yield from ["Horsepower ", "is torque x rpm ", "/ 5252 [1]."]

    monkeypatch.setattr(llm_client, "call_llm", fake_call)
    monkeypatch.setattr(llm_client, "stream_llm", fake_stream)
    return calls


def test_irrelevant_question_skips_llm(kb, llm):
    resp = rag.answer("best pizza recipe in naples")
    assert resp.found is False and resp.sources == []
    assert "couldn't find" in resp.answer and "example.com/contact" in resp.answer
    assert llm == []  # no quota spent


def test_empty_knowledge_base_is_not_found(store, llm):
    assert rag.answer("horsepower torque").found is False
    assert llm == []


def test_answer_uses_numbered_context_and_cites(kb, llm):
    resp = rag.answer("how do I calculate horsepower from torque and rpm")
    assert resp.found and resp.answer.endswith("[1].")
    system = llm[0][0]["content"]
    assert "[1] Horsepower calculator" in system and "5252" in system
    assert "Test Bot" in system
    assert [s.index for s in resp.sources] == [1]  # only cited sources returned
    assert resp.sources[0].url == "https://s.test/hp"
    assert "5252" in resp.sources[0].snippet and not resp.sources[0].snippet.startswith("Horsepower\n")


def test_history_is_included_and_trimmed(kb, llm):
    history = [ChatTurn(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
               for i in range(10)]
    rag.answer("horsepower torque rpm", history)
    messages = llm[0]
    assert messages[0]["role"] == "system" and messages[-1]["content"] == "horsepower torque rpm"
    assert [m["content"] for m in messages[1:-1]] == [f"turn {i}" for i in range(4, 10)]


def test_follow_up_uses_previous_question_for_retrieval(kb, llm):
    history = [ChatTurn(role="user", content="explain compression ratio swept volume"),
               ChatTurn(role="assistant", content="It compares volumes [1].")]
    retrieval = rag.retrieve("and the chamber?", history)
    assert retrieval.found and retrieval.sources[0].source == "https://s.test/cr"


def test_cited_indices_ignores_unknown_numbers(kb):
    sources = rag.retrieve("horsepower torque rpm").sources
    assert rag.cited_indices("See [1] and [7] and [1] again.", sources) == [1]


def test_stream_event_order(kb, llm):
    events = list(rag.stream_answer("horsepower torque rpm"))
    assert events[0]["type"] == "sources" and events[0]["found"] is True
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Horsepower is torque x rpm / 5252 [1]."
    assert events[-1] == {"type": "done", "cited": [1]}


def test_stream_not_found(kb, llm):
    events = list(rag.stream_answer("pizza dough hydration"))
    assert events[0] == {"type": "sources", "sources": [], "found": False}
    assert events[-1]["type"] == "done" and llm == []


def test_uncited_answer_returns_no_sources(kb, monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm", lambda m: "I could not find that in the documentation.")
    assert rag.answer("horsepower torque rpm").sources == []
