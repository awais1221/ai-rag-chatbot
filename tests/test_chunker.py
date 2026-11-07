from app.chunker import chunk_documents, split_sections, split_text
from app.schemas import Document


def test_short_text_is_one_chunk():
    assert split_text("Hello world.", 100, 10) == ["Hello world."]


def test_empty_text_gives_no_chunks():
    assert split_text("   \n ", 100, 10) == []


def test_long_text_is_split_within_size():
    text = " ".join(f"Sentence number {i} talks about engines." for i in range(200))
    chunks = split_text(text, 300, 50)
    assert len(chunks) > 5
    assert all(len(c) <= 310 for c in chunks)


def test_chunks_overlap():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 30 for i in range(10))
    chunks = split_text(text, 400, 120)
    # the end of one chunk reappears at the start of the next
    for a, b in zip(chunks, chunks[1:]):
        assert b.split()[0] in a


def test_all_text_is_kept():
    words = [f"w{i}" for i in range(500)]
    chunks = split_text(" ".join(words), 200, 0)
    joined = " ".join(chunks)
    assert all(w in joined for w in words)


def test_very_long_word_is_hard_cut():
    chunks = split_text("x" * 1000, 300, 0)
    assert all(len(c) <= 300 for c in chunks)
    assert sum(len(c) for c in chunks) == 1000


def test_split_sections_by_heading():
    text = "Intro text\n\n# Title\n\nBody one\n\n## Sub\n\nBody two"
    assert split_sections(text) == [("", "Intro text"), ("Title", "Body one"), ("Sub", "Body two")]


def test_chunks_get_heading_prefix_and_metadata():
    doc = Document(text="# Horsepower\n\nHP = torque x rpm / 5252.\n\n# Torque\n\nA turning force.",
                   source="https://site/hp", title="HP page", source_type="url")
    chunks = chunk_documents([doc], size=800, overlap=100)
    assert [c.heading for c in chunks] == ["Horsepower", "Torque"]
    assert chunks[0].text.startswith("Horsepower\n")
    assert chunks[1].chunk_index == 1
    assert all(c.source == "https://site/hp" and c.source_type == "url" for c in chunks)


def test_empty_sections_are_skipped():
    doc = Document(text="# Empty\n\n# Full\n\nSome content here.", source="a", title="a")
    chunks = chunk_documents([doc])
    assert len(chunks) == 1 and chunks[0].heading == "Full"
