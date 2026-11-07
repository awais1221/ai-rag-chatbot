"""Split documents into overlapping, heading-aware chunks for embedding.

1. Split each document into sections at Markdown headings (``# ...``).
2. Split long sections recursively: paragraphs -> lines -> sentences -> words,
   then merge the pieces back into chunks of about ``chunk_size`` characters
   with ``chunk_overlap`` characters of overlap.
3. Each chunk starts with its section heading, so a chunk taken out of
   context still says what it is about (this noticeably improves retrieval).
"""

from __future__ import annotations

import re

from app.schemas import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SEPARATORS = ["\n\n", "\n", ". ", " "]


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return [(heading, body), ...]. Text before the first heading has heading ''."""
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [("", text.strip())] if text.strip() else []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end(): end].strip()
        sections.append((match.group(2).strip(), body))
    return sections


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Recursively split text into pieces of at most ~size chars with overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    return _merge(_atoms(text, size, 0), size, overlap)


def _atoms(text: str, size: int, level: int) -> list[str]:
    """Break text into pieces no longer than size, using coarser separators first."""
    if len(text) <= size:
        return [text]
    if level >= len(_SEPARATORS):
        return [text[i: i + size] for i in range(0, len(text), size)]  # hard cut
    sep = _SEPARATORS[level]
    parts = text.split(sep)
    if len(parts) == 1:
        return _atoms(text, size, level + 1)
    pieces = []
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        # keep the separator attached so sentences / paragraphs stay readable
        part = part + (sep if i < len(parts) - 1 else "")
        pieces.extend(_atoms(part, size, level + 1))
    return pieces


def _merge(pieces: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    fresh = 0  # pieces in `current` not yet emitted in any chunk
    for piece in pieces:
        if current and length + len(piece) > size:
            chunks.append("".join(current).strip())
            fresh = 0
            # carry trailing pieces over as overlap
            while current and (length > overlap or length + len(piece) > size):
                length -= len(current[0])
                current.pop(0)
        current.append(piece)
        length += len(piece)
        fresh += 1
    if current and fresh:  # skip a tail that is only overlap
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def chunk_documents(docs: list[Document], size: int = 800, overlap: int = 120) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        index = 0
        for heading, body in split_sections(doc.text):
            if not body:
                continue
            prefix = f"{heading}\n" if heading else ""
            for piece in split_text(body, max(100, size - len(prefix)), overlap):
                chunks.append(Chunk(
                    text=prefix + piece,
                    source=doc.source,
                    title=doc.title,
                    source_type=doc.source_type,
                    chunk_index=index,
                    page=doc.page,
                    heading=heading,
                ))
                index += 1
    return chunks
