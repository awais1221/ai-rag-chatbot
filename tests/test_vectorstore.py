import os

import pytest

from app.chunker import chunk_documents
from app.schemas import Document


def _docs():
    return [
        Document(text="Horsepower equals torque times rpm divided by 5252.",
                 source="https://s.test/hp", title="HP", source_type="url"),
        Document(text="Compression ratio compares cylinder volume at bottom and top dead centre.",
                 source="https://s.test/cr", title="CR", source_type="url"),
        Document(text="Piston speed depends on stroke length and engine rpm.",
                 source="manual.pdf", title="manual.pdf (p. 3)", source_type="pdf", page=3),
    ]


def test_add_and_search_ranks_relevant_first(store):
    assert store.add_chunks(chunk_documents(_docs())) == 3
    hits = store.search("what is horsepower torque rpm", k=3)
    assert hits[0].source == "https://s.test/hp"
    assert 0 < hits[0].score <= 1 and hits[0].score > hits[-1].score
    pdf_hit = next(h for h in hits if h.source == "manual.pdf")
    assert pdf_hit.page == 3


def test_search_empty_store(store):
    assert store.search("anything", k=4) == []


def test_reindexing_replaces_instead_of_duplicating(store):
    store.add_chunks(chunk_documents(_docs()))
    updated = Document(text="Updated horsepower page.", source="https://s.test/hp", title="HP v2")
    store.replace_source("https://s.test/hp", chunk_documents([updated]))
    assert store.count() == 3
    titles = {s.source: s.title for s in store.list_sources()}
    assert titles["https://s.test/hp"] == "HP v2"


def test_delete_list_and_reset(store):
    store.add_chunks(chunk_documents(_docs()))
    assert {s.source for s in store.list_sources()} == {"https://s.test/hp", "https://s.test/cr", "manual.pdf"}
    assert store.delete_source("https://s.test/cr") == 1
    assert store.delete_source("missing") == 0
    store.reset()
    assert store.count() == 0 and store.list_sources() == []


@pytest.mark.skipif(os.getenv("SKIP_SLOW") == "1", reason="downloads the embedding model")
def test_real_embedding_model_understands_meaning(tmp_path):
    """Uses the real local fastembed model: matches by meaning, not shared words."""
    from app.vectorstore import FastEmbedder, VectorStore

    vs = VectorStore(str(tmp_path / "real"), "real", FastEmbedder("BAAI/bge-small-en-v1.5"))
    vs.add_chunks(chunk_documents(_docs()))
    hits = vs.search("How do I convert torque into power?", k=3)
    assert hits[0].score > 0.6
    assert vs.search("best pizza recipe", k=1)[0].score < 0.55  # off-topic stays below MIN_SCORE
    assert hits[0].source == "https://s.test/hp"


def test_get_store_is_created_once_under_concurrency(monkeypatch):
    import threading
    import time

    from app import vectorstore

    created = []

    class SlowStore:
        def __init__(self, *args):
            time.sleep(0.2)  # like loading the embedding model
            created.append(self)

    monkeypatch.setattr(vectorstore, "_store", None)
    monkeypatch.setattr(vectorstore, "VectorStore", SlowStore)
    monkeypatch.setattr(vectorstore, "FastEmbedder", lambda name: None)
    results = []
    threads = [threading.Thread(target=lambda: results.append(vectorstore.get_store())) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(created) == 1 and all(r is created[0] for r in results)
