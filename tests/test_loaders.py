import io

import httpx
import pytest

from app.loaders import LoaderError, WebLoader, html_to_text, load_file


# ------------------------------------------------------------------ files
def test_load_txt():
    docs = load_file("notes.txt", "Hello there, this is a note.".encode())
    assert docs[0].text.startswith("Hello") and docs[0].source == "notes.txt"


def test_load_md_with_cp1252_bytes():
    docs = load_file("a.md", "# Caf\xe9 menu".encode("cp1252"))
    assert "Café" in docs[0].text


@pytest.mark.parametrize("name,data,msg", [
    ("x.exe", b"MZ...", "Unsupported"),
    ("x.txt", b"", "empty"),
    ("x.pdf", b"not a pdf", "valid PDF"),
    ("x.docx", b"not a zip", "valid DOCX"),
    ("x.txt", b"   \n  ", "No readable text"),
])
def test_load_file_rejects_bad_input(name, data, msg):
    with pytest.raises(LoaderError, match=msg):
        load_file(name, data)


def test_load_file_rejects_oversize(monkeypatch):
    from app import loaders
    from app.config import get_settings

    small = get_settings().__class__(base_url="", api_key="", model_name="", max_file_mb=0)
    monkeypatch.setattr(loaders, "get_settings", lambda: small)
    with pytest.raises(LoaderError, match="larger"):
        load_file("x.txt", b"some text")


def test_load_pdf_one_document_per_page():
    import pymupdf as fitz

    pdf = fitz.open()
    for text in ("First page about pistons and rods.", "Second page about compression."):
        pdf.new_page().insert_text((72, 72), text)
    docs = load_file("manual.pdf", pdf.tobytes())
    assert [d.page for d in docs] == [1, 2]
    assert docs[1].title == "manual.pdf (p. 2)" and "compression" in docs[1].text


def test_load_docx_keeps_headings_and_tables():
    import docx

    d = docx.Document()
    d.add_heading("Returns policy", level=1)
    d.add_paragraph("You can return items within 30 days.")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Shipping"
    table.rows[0].cells[1].text = "Free over $50"
    buf = io.BytesIO()
    d.save(buf)
    text = load_file("policy.docx", buf.getvalue())[0].text
    assert "# Returns policy" in text
    assert "30 days" in text and "Shipping | Free over $50" in text


# ------------------------------------------------------------------ html
HTML = """<html><head><title>HP Calculator</title><script>var x=1;</script></head>
<body><nav><a href="/">Home</a> Menu stuff</nav>
<main><h1>Horsepower</h1><p>HP = torque x rpm / 5252.</p>
<ul><li><p>Crank power</p></li><li>Wheel power</li></ul>
<table><tr><th>Torque</th><th>HP</th></tr><tr><td>450</td><td>428</td></tr></table>
<form><button>Calculate</button></form></main>
<footer>Copyright</footer></body></html>"""


def test_html_to_text_keeps_content_drops_noise():
    title, text = html_to_text(HTML)
    assert title == "HP Calculator"
    assert "# Horsepower" in text
    assert "- Crank power" in text and text.count("Crank power") == 1
    assert "Torque | HP" in text and "450 | 428" in text
    for noise in ("Menu stuff", "var x", "Copyright", "Calculate"):
        assert noise not in text


# ------------------------------------------------------------------ web
def _site(pages: dict[str, tuple[int, str, str]]) -> WebLoader:
    def handler(request: httpx.Request) -> httpx.Response:
        status, ctype, body = pages.get(str(request.url), (404, "text/plain", "nope"))
        return httpx.Response(status, headers={"content-type": ctype}, text=body)

    return WebLoader(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)


def _page(title, body, links=""):
    return (200, "text/html", f"<html><head><title>{title}</title></head><body><main>"
                              f"<h1>{title}</h1><p>{body}</p>{links}</main></body></html>")


SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://s.test/a</loc></url><url><loc>https://s.test/b</loc></url></urlset>"""


def test_sitemap_loads_every_page():
    loader = _site({
        "https://s.test/sitemap.xml": (200, "application/xml", SITEMAP),
        "https://s.test/a": _page("Page A", "All about displacement and bore size."),
        "https://s.test/b": _page("Page B", "All about compression ratio maths."),
    })
    docs, errors = loader.load_url("https://s.test/sitemap.xml")
    assert [d.title for d in docs] == ["Page A", "Page B"] and errors == []


def test_site_root_prefers_sitemap_and_reports_failures():
    loader = _site({
        "https://s.test/sitemap.xml": (200, "application/xml", SITEMAP),
        "https://s.test/a": _page("Page A", "All about displacement and bore size."),
    })
    docs, errors = loader.load_url("https://s.test/")
    assert [d.source for d in docs] == ["https://s.test/a"]
    assert errors[0][0] == "https://s.test/b" and "404" in errors[0][1]


def test_sitemap_index_is_followed():
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <sitemap><loc>https://s.test/pages.xml</loc></sitemap></sitemapindex>"""
    loader = _site({
        "https://s.test/index.xml": (200, "application/xml", index),
        "https://s.test/pages.xml": (200, "application/xml", SITEMAP),
    })
    assert loader.sitemap_urls("https://s.test/index.xml", 10) == ["https://s.test/a", "https://s.test/b"]


def test_crawl_stays_on_domain_and_respects_limit():
    links = ('<a href="/b">b</a><a href="https://other.test/x">ext</a>'
             '<a href="/file.pdf">pdf</a><a href="/c#top">c</a>')
    loader = _site({
        "https://s.test/": _page("Home", "Welcome to the calculator site.", links),
        "https://s.test/b": _page("B", "Page b content here."),
        "https://s.test/c": _page("C", "Page c content here."),
    })
    assert loader.crawl("https://s.test/", 10) == ["https://s.test/", "https://s.test/b", "https://s.test/c"]
    assert len(loader.crawl("https://s.test/", 2)) == 2


def test_robots_txt_is_respected():
    loader = _site({
        "https://s.test/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private"),
        "https://s.test/private": _page("Secret", "Hidden content here."),
    })
    with pytest.raises(LoaderError, match="robots"):
        loader.load_url("https://s.test/private")


def test_non_html_page_and_bad_url():
    loader = _site({"https://s.test/data": (200, "application/json", '{"a": 1}')})
    with pytest.raises(LoaderError, match="not an HTML"):
        loader.load_url("https://s.test/data")
    with pytest.raises(LoaderError, match="http"):
        loader.load_url("ftp://s.test/file")
