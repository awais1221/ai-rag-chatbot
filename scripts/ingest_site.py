"""Index a website, sitemap, page or local files from the command line.

Examples:
    python -m scripts.ingest_site https://figureshed.com/
    python -m scripts.ingest_site https://example.com/sitemap.xml --max-pages 100
    python -m scripts.ingest_site https://example.com/faq --mode page
    python -m scripts.ingest_site docs/manual.pdf docs/faq.md
    python -m scripts.ingest_site --list
    python -m scripts.ingest_site https://example.com/ --reset   # start fresh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.chunker import chunk_documents
from app.config import get_settings
from app.loaders import LoaderError, WebLoader, load_file
from app.logging_config import setup_logging
from app.vectorstore import get_store


def main() -> int:
    parser = argparse.ArgumentParser(description="Add content to the chatbot's knowledge base.")
    parser.add_argument("targets", nargs="*", help="URLs and/or local file paths")
    parser.add_argument("--mode", choices=["auto", "page", "sitemap", "crawl"], default="auto")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="delete everything first")
    parser.add_argument("--list", action="store_true", help="show what is indexed")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
    setup_logging()

    settings = get_settings()
    store = get_store()
    if args.reset:
        store.reset()
        print("Knowledge base cleared.")

    loader = WebLoader()
    total = 0
    for target in args.targets:
        try:
            if target.startswith(("http://", "https://")):
                docs, errors = loader.load_url(target, args.mode, args.max_pages)
            else:
                path = Path(target)
                docs, errors = load_file(path.name, path.read_bytes()), []
        except (LoaderError, OSError) as exc:
            print(f"  ✗ {target}: {exc}")
            continue
        by_source: dict[str, list] = {}
        for doc in docs:
            by_source.setdefault(doc.source, []).append(doc)
        for source, group in by_source.items():
            count = store.replace_source(
                source, chunk_documents(group, settings.chunk_size, settings.chunk_overlap))
            total += count
            print(f"  ✓ {group[0].title[:70]:70s} {count:>3} chunks")
        for url, err in errors:
            print(f"  ✗ {url}: {err}")

    if args.targets:
        print(f"\nAdded {total} chunks. Knowledge base now holds {store.count()} chunks.")
    if args.list or not args.targets:
        for s in store.list_sources():
            print(f"  {s.chunks:>4}  {s.title}  ({s.source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
