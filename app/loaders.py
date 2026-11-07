"""Turn files and web pages into plain-text ``Document`` objects.

Supported: PDF (one Document per page, so citations can name the page),
DOCX, TXT/MD, single web pages, XML sitemaps (incl. sitemap indexes) and a
small same-domain crawler for sites without a sitemap.

Web text is converted to light Markdown (``# Heading`` lines, ``a | b`` table
rows) so the chunker can split on headings.
"""

from __future__ import annotations

import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.schemas import Document

logger = logging.getLogger(__name__)

USER_AGENT = "RAGChatbotIndexer/1.0 (+https://github.com/awais1221/ai-rag-chatbot)"
FILE_EXTENSIONS = {"pdf", "docx", "txt", "md"}
MAX_PAGE_BYTES = 5 * 1024 * 1024
CRAWL_DELAY = 0.2  # seconds between requests, be polite to the site
_SKIP_LINK_EXT = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|webp|ico|css|js|json|xml|zip|mp4|mp3|woff2?|ttf)$", re.I
)
_NOISE_TAGS = ["script", "style", "noscript", "svg", "nav", "footer", "aside",
               "form", "button", "iframe", "template"]
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote",
               "tr", "dt", "dd", "figcaption"]


class LoaderError(Exception):
    """A file or page could not be loaded (message is safe to show users)."""


# ======================================================================= files
def load_file(filename: str, data: bytes) -> list[Document]:
    """Validate an uploaded file and extract its text."""
    settings = get_settings()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in FILE_EXTENSIONS:
        raise LoaderError(f"Unsupported file type '.{ext}'. Use PDF, DOCX, TXT or MD.")
    if not data:
        raise LoaderError("The file is empty.")
    if len(data) > settings.max_file_bytes:
        raise LoaderError(f"File is larger than {settings.max_file_mb} MB.")

    if ext == "pdf":
        if not data.startswith(b"%PDF"):
            raise LoaderError("This does not look like a valid PDF file.")
        docs = _load_pdf(filename, data)
    elif ext == "docx":
        if not data.startswith(b"PK"):
            raise LoaderError("This does not look like a valid DOCX file.")
        docs = _load_docx(filename, data)
    else:
        text = _decode(data)
        docs = [Document(text=text, source=filename, title=filename)] if text.strip() else []

    if not docs:
        raise LoaderError(
            "No readable text found. (Scanned PDFs / images are not supported yet.)"
        )
    return docs


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_pdf(filename: str, data: bytes) -> list[Document]:
    import pymupdf as fitz

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise LoaderError("Could not open the PDF (it may be corrupted or encrypted).") from exc
    docs = []
    with pdf:
        for number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if len(text) >= 20:
                docs.append(Document(text=text, source=filename,
                                     title=f"{filename} (p. {number})",
                                     source_type="pdf", page=number))
    return docs


def _load_docx(filename: str, data: bytes) -> list[Document]:
    import docx  # python-docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise LoaderError("Could not open the DOCX file.") from exc

    lines = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        match = re.match(r"heading (\d)", style)
        lines.append(f"{'#' * int(match.group(1))} {text}" if match else text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    text = "\n\n".join(lines)
    if not text.strip():
        return []
    return [Document(text=text, source=filename, title=filename, source_type="docx")]


# ======================================================================= web
def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, markdown-ish text) for an HTML page, without menus/footers."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    root = soup.find("main") or soup.find("article") or soup.body or soup
    h1 = root.find("h1")
    if not title and h1:
        title = h1.get_text(" ", strip=True)

    lines: list[str] = []
    for el in root.find_all(_BLOCK_TAGS):
        if el.find_parent(_BLOCK_TAGS):
            continue  # already included via its parent (e.g. <p> inside <li>)
        if el.name == "tr":
            cells = [c.get_text(" ", strip=True) for c in el.find_all(["td", "th"])]
            text = " | ".join(c for c in cells if c)
        else:
            text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if not text:
            continue
        if el.name[0] == "h" and el.name[1:].isdigit():
            text = f"{'#' * int(el.name[1])} {text}"
        elif el.name == "li":
            text = f"- {text}"
        lines.append(text)

    body = "\n\n".join(lines)
    if len(body) < 50:  # div-soup site without semantic tags
        body = re.sub(r"\n\s*\n+", "\n\n", root.get_text("\n", strip=True))
    return title or "Untitled page", body


class WebLoader:
    """Fetches pages politely (robots.txt, delay, size cap)."""

    def __init__(self, client: httpx.Client | None = None, delay: float = CRAWL_DELAY):
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=15
        )
        self.delay = delay
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0

    # ------------------------------------------------------------ fetching
    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser = robotparser.RobotFileParser()
            try:
                resp = self.client.get(f"{origin}/robots.txt")
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                self._robots[origin] = parser
            except httpx.HTTPError:
                self._robots[origin] = None  # robots unreachable: allow
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def fetch(self, url: str) -> tuple[str, str]:
        """GET a URL and return (content_type, text). Raises LoaderError."""
        _check_url(url)
        if not self._allowed(url):
            raise LoaderError(f"Blocked by the site's robots.txt: {url}")
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as exc:
            raise LoaderError(f"Could not fetch {url} ({type(exc).__name__}).") from exc
        finally:
            self._last_request = time.monotonic()
        if resp.status_code >= 400:
            raise LoaderError(f"{url} returned HTTP {resp.status_code}.")
        if len(resp.content) > MAX_PAGE_BYTES:
            raise LoaderError(f"{url} is larger than 5 MB.")
        return resp.headers.get("content-type", "").lower(), resp.text

    # ------------------------------------------------------------ loading
    def load_page(self, url: str) -> Document:
        content_type, body = self.fetch(url)
        if "html" not in content_type and not body.lstrip().lower().startswith(("<!doctype", "<html")):
            raise LoaderError(f"{url} is not an HTML page ({content_type or 'unknown type'}).")
        title, text = html_to_text(body)
        if len(text.strip()) < 30:
            raise LoaderError(f"No readable text found on {url}.")
        return Document(text=text, source=url, title=title, source_type="url")

    def sitemap_urls(self, sitemap_url: str, limit: int, _depth: int = 0) -> list[str]:
        _, body = self.fetch(sitemap_url)
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError as exc:
            raise LoaderError(f"{sitemap_url} is not a valid XML sitemap.") from exc
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
        if root.tag.endswith("sitemapindex"):
            if _depth >= 1:
                return []
            urls: list[str] = []
            for child in locs:
                if len(urls) >= limit:
                    break
                try:
                    urls += self.sitemap_urls(child, limit - len(urls), _depth + 1)
                except LoaderError as exc:
                    logger.warning("Skipping child sitemap: %s", exc)
            return urls[:limit]
        return locs[:limit]

    def crawl(self, start_url: str, limit: int) -> list[str]:
        """Breadth-first discovery of same-domain HTML pages (returns URLs)."""
        start_url = _normalize(start_url)
        domain = urlparse(start_url).netloc
        queue, seen, found = deque([start_url]), {start_url}, []
        while queue and len(found) < limit:
            url = queue.popleft()
            try:
                content_type, body = self.fetch(url)
            except LoaderError as exc:
                logger.info("Crawl skip: %s", exc)
                continue
            if "html" not in content_type:
                continue
            found.append(url)
            for a in BeautifulSoup(body, "html.parser").find_all("a", href=True):
                link = _normalize(urljoin(url, a["href"]))
                parsed = urlparse(link)
                if (parsed.scheme in ("http", "https") and parsed.netloc == domain
                        and link not in seen and not _SKIP_LINK_EXT.search(parsed.path)):
                    seen.add(link)
                    queue.append(link)
        return found

    def load_url(self, url: str, mode: str = "auto",
                 max_pages: int | None = None) -> tuple[list[Document], list[tuple[str, str]]]:
        """Load a page, sitemap or crawl. Returns (documents, [(url, error), ...])."""
        _check_url(url)
        limit = min(max_pages or get_settings().max_crawl_pages, 500)
        path = urlparse(url).path

        if mode == "auto":
            if path.lower().endswith(".xml"):
                mode = "sitemap"
            elif path in ("", "/"):
                # Site root: prefer its sitemap, otherwise crawl from the homepage.
                origin = url.rstrip("/")
                try:
                    urls = self.sitemap_urls(f"{origin}/sitemap.xml", limit)
                except LoaderError:
                    urls = []
                return self._load_many(urls or self.crawl(url, limit))
            else:
                mode = "page"

        if mode == "page":
            return [self.load_page(url)], []
        if mode == "sitemap":
            urls = self.sitemap_urls(url, limit)
            if not urls:
                raise LoaderError("The sitemap does not list any pages.")
            return self._load_many(urls)
        return self._load_many(self.crawl(url, limit))

    def _load_many(self, urls: list[str]) -> tuple[list[Document], list[tuple[str, str]]]:
        docs, errors = [], []
        for url in dict.fromkeys(urls):  # de-duplicate, keep order
            try:
                docs.append(self.load_page(url))
            except LoaderError as exc:
                errors.append((url, str(exc)))
        if not docs and not errors:
            raise LoaderError("No pages found to index.")
        return docs, errors


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LoaderError("Enter a full web address starting with http:// or https://")


def _normalize(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
