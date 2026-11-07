# RAG Chatbot (Site Assistant): Implementation Plan

## Context
This is the second Upwork portfolio project. The profile sells "AI integrations: chatbots", and nothing in the portfolio shows that yet. The bot is a RAG chatbot that answers questions from a business's own documents or website, with cited sources. It comes with an **embeddable chat widget**, and that widget goes live on the user's deployed **Figureshed** site (a static Next.js site with engine and drag-racing calculators). That gives the portfolio a real-world demo instead of fake data.
**Constraint: $0 spend.** Free LLM tier, local embeddings and free hosting only.

New repo: `D:\oslabs\python_programming\Simple python\rag-chatbot\` (separate git repo, later pushed to GitHub as `ai-rag-chatbot`).

## Stack (mirrors invoice-extractor so the code style is consistent)
- **FastAPI** backend, plain HTML/CSS/JS frontend (same approach as `invoice-extractor/static/`)
- **LLM**: OpenAI-compatible client with Gemini free tier as the default. Groq, GitHub Models and Ollama work by editing `.env`.
- **Embeddings**: `fastembed` (`BAAI/bge-small-en-v1.5`). It runs locally on ONNX, needs no torch, is free and has no rate limits.
- **Vector store**: ChromaDB (persistent, on local disk). It's a well-known name that clients search for.
- **Ingestion**: PDF (PyMuPDF), TXT/MD, DOCX (python-docx), and **a website URL or sitemap crawl** (httpx + BeautifulSoup). The crawler is the part clients want most: "train it on my website".
- **Hosting (free)**: Hugging Face Spaces (Docker SDK, free CPU). There's a `Dockerfile` in the repo.

## Reuse from invoice-extractor (copy and adapt, don't rewrite)
- `app/config.py`: frozen `Settings` dataclass with `_get_int` / `_get_float` and the `llm_configured` placeholder check.
- `app/llm_client.py`: client caching, 429 retry with `_retry_delay`, `_human_duration` and `_friendly_error`. Changes: remove JSON mode, add `stream_llm(messages)` (a generator using `stream=True`), and use a small non-zero temperature.
- `app/logging_config.py`: unchanged.
- `.env.example` provider block, `.gitignore` and the README layout (screenshots, GIF, How it works, Limitations).

## Folder structure
```
rag-chatbot/
├── app/
│   ├── main.py          # routes, CORS, static, error handlers
│   ├── config.py        # + CHUNK_SIZE, TOP_K, ADMIN_TOKEN, ALLOWED_ORIGINS, rate limits
│   ├── llm_client.py    # adapted, + stream_llm()
│   ├── loaders.py       # pdf/txt/md/docx/url/sitemap -> list[Document(text, source, title)]
│   ├── chunker.py       # heading-aware recursive splitter (~800 chars, 120 overlap)
│   ├── vectorstore.py   # Chroma + fastembed wrapper: add, search, list_sources, delete_source, reset
│   ├── rag.py           # retrieve -> score threshold -> prompt -> answer + citations
│   ├── ratelimit.py     # in-memory per-IP sliding window + global daily cap
│   ├── schemas.py       # ChatRequest, ChatResponse, Source, IngestResult
│   └── logging_config.py
├── static/
│   ├── index.html       # full app: chat panel + "Knowledge base" sidebar (upload, URL, source list)
│   ├── app.js / style.css
│   ├── widget.js        # self-contained embeddable bubble (Shadow DOM, no deps)
│   └── widget-demo.html # a sample "client website" showing the one-line embed
├── scripts/ingest_site.py   # CLI: python -m scripts.ingest_site https://figureshed.com/sitemap.xml
├── tests/ (test_chunker, test_loaders, test_vectorstore, test_rag, test_api, test_ratelimit)
├── Dockerfile, requirements.txt, .env.example, README.md, PLAN.md, LICENSE
```

## Key behaviour
**RAG pipeline (`rag.py`)**
1. Embed the question and fetch the top-K chunks (K=4).
2. If the best match is below a similarity threshold, skip the LLM call and reply "I couldn't find that in the documentation", with a contact-page suggestion. This saves free quota and stops made-up answers.
3. The prompt holds numbered context blocks. The system prompt says to answer only from the context, cite as [1], [2], and admit when the answer isn't there.
4. The last 3 conversation turns are included so follow-up questions work. History is kept on the client side, so the server stays stateless.
5. The response gives the answer plus `sources: [{title, url/filename, snippet}]`, rendered as clickable citation chips.

**Streaming**: `POST /chat/stream` uses SSE, so the answer appears token by token. A non-streaming `POST /chat` exists for tests and API users.

**Endpoints**
- Public: `GET /`, `GET /health`, `POST /chat`, `POST /chat/stream`, `GET /widget.js`
- Admin (requires the `X-Admin-Token` header): `POST /ingest/files`, `POST /ingest/url` (single page or sitemap, max-pages cap), `GET /sources`, `DELETE /sources/{id}`, `POST /reset`
- On the live Figureshed widget, visitors can only chat. Uploading and changing the knowledge base is admin-only.

**Abuse protection** (matters because the widget is public and runs on a free key):
- Per IP: 8 messages a minute and 60 a day. Global: `DAILY_CAP=500`. Questions are capped at 500 characters.
- CORS is limited to `ALLOWED_ORIGINS` (the Figureshed domain plus localhost).
- When a limit is hit, the user sees a friendly "Demo limit reached" message, never a stack trace.

**Widget (`widget.js`)**: embedded with one line:
`<script src="https://<space>.hf.space/widget.js" data-title="Figureshed Assistant" data-color="#…" defer></script>`
It's a floating bubble with a chat panel and streaming replies, and it shows suggested starter questions. It uses Shadow DOM so the host site's CSS can't break it. It's mobile friendly and has no dependencies.

## Figureshed integration (separate step, done only with your go-ahead)
1. Confirm the live URL of Figureshed (`.env.example` says `figureshed.com`).
2. Crawl the live site's `sitemap.xml` into the knowledge base. The FAQ and how-it-works text in `content/*.ts` gets indexed this way.
3. Add the script tag to `Tools Site Project/app/layout.tsx` behind an env flag (`NEXT_PUBLIC_CHAT_WIDGET_URL`). If the flag is empty, the site is unchanged. Then redeploy on Vercel.
4. Free-host caveat: HF Spaces sleeps after about 48 hours with no traffic, and the first message after that takes about 30 to 60 seconds. The widget shows "Waking up the assistant…" and loads lazily on first open, so it never slows down the page.

## Build order
1. Scaffold the repo and venv, copy and adapt config, llm_client and logging.
2. loaders + chunker + their tests.
3. vectorstore (Chroma + fastembed) + test (uses a temp directory).
4. rag.py + test_rag (call_llm mocked).
5. main.py, ratelimit and test_api (TestClient, mocked LLM).
6. static/index.html app UI, then widget.js and widget-demo.html.
7. scripts/ingest_site.py, then ingest Figureshed locally and test real questions.
8. Dockerfile, README with screenshots and GIF, PLAN.md.
9. (Ask first) Deploy to HF Spaces, then the Figureshed embed change.

## Verification
- `pytest -v`: all tests pass with no API key (LLM mocked; the embeddings model runs locally).
- `uvicorn app.main:app --reload`: upload a sample PDF, ask a question, check that the cited answer streams in and that clicking a citation shows the snippet.
- Ingest the Figureshed sitemap and ask "Why do HP and torque cross at 5252 rpm?" (it should cite the horsepower page). Then ask something off-topic, which should get the polite "not in the docs" reply without an LLM call.
- Open `widget-demo.html` and check that the bubble works and the CORS rules apply.
- Fire 10 quick requests and confirm the rate-limit message.
- Without a key: the UI loads, ingestion works, and chat returns a clear "LLM not configured" message.

## Limitations to note in the README
Free-tier rate limits, cold starts on HF Spaces, the in-memory rate limiter resets on restart, no auth beyond the admin token, and scanned PDFs aren't OCR'd. Future work: OCR, multi-tenant knowledge bases, a lead-capture form, a Telegram channel.
