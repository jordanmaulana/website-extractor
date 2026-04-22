# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency / environment management uses `uv`. Most workflows go through the Makefile:

- `make dev` — run the Django dev server on port 8000 (`uv run manage.py runserver 8000`)
- `make lint` — `ruff format .` then `ruff check . --fix`. A Windsurf rule (`.windsurf/rules/python.md`) requires running this after every code change; resolve all errors before stopping.
- `make mmg` / `make migrate` — `makemigrations` / `migrate`
- `make upgrade` — `uv sync && uv lock --upgrade && uv sync --frozen --no-install-project`
- `make tw` — Tailwind watcher (`npx @tailwindcss/cli -i ./static/input.css -o ./static/output.css --watch`)
- `make test` — runs the `scrape_url` management command against `https://uhudtour.com/` as a live scrape. This is **not** the test suite.

Real test suite (pytest + pytest-django; `testpaths = ["scrapes/tests"]`, `DJANGO_SETTINGS_MODULE = "core.settings"` both configured in `pyproject.toml`):

- All tests: `uv run pytest`
- Single file: `uv run pytest scrapes/tests/test_rag.py`
- Single test: `uv run pytest scrapes/tests/test_rag.py::test_name`

Scrapes / RAG entry points (Django management commands):

- Scrape a URL: `uv run manage.py scrape_url <url> [--no-recursive] [--max-depth N] [--include-images] [--selenium] [--save-json]`
- Index scraped content for RAG: `uv run manage.py index_websites [--scrape-id <id>]`
- Ad-hoc RAG query: `uv run python example_rag_query.py`
- RAG retrieval eval: `uv run python scrapes/eval/run_eval.py --phase phase1 [--top-k 5] [--only id1,id2]` — reads `scrapes/eval/queries.yaml`, writes `scrapes/eval/results/<phase>_<YYYY-MM-DD>.json` with recall@1 / recall@k.

The README still references a standalone `main.py` CLI — that entry point no longer exists. Use the Django management commands above.

## Architecture

This is a **Django 5.2 project** (`core/` is the project package) that wraps a website scraper plus a RAG pipeline on **Postgres + pgvector**. There is no frontend or REST routing — `core/urls.py` only mounts `/admin/`. All work happens through management commands, pytest, or the Django admin.

### Apps

- **`core/`** — Django project + shared models. `core/models.py` defines `BaseModel` (abstract): every domain model inherits and gets a **string `bson.ObjectId` primary key** (via `make_object_id`), plus `created_on`, `updated_on`, and an optional `actor` FK to `User`. Default ordering is by `id`; there's a `created_on` index. Also defines `AppSetting`, a typed key/value store with a `.get(key, value_type, default)` helper (`value_type` in `{str,int,float,bool}`).
- **`scrapes/`** — scraper + RAG. Four models in `scrapes/models.py`:
  - `Scrape` — one row per top-level URL the user asked to scrape (unique on `url`).
  - `Website` — one row per page under a `Scrape` (unique together on `(url, scrape)`). Holds markdown `content`, `images` JSON list, and indexing state (`content_hash`, `indexed_with_model`). `is_indexed` is a computed property based on `indexed_with_model`.
  - `Chunk` — one row per indexed chunk of a `Website`. Carries `text`, `token_count`, `heading_path`, `embedding_model`, a pgvector `embedding` (dims from `settings.RAG["EMBEDDING_DIMS"]`), and a Postgres `SearchVectorField` for FTS. Has an **HNSW index** on `embedding` (cosine ops, m=16, ef_construction=64) and a **GIN index** on `search_vector`. Unique together on `(website, chunk_index)`.
  - `RAGQueryLog` — audit row written for every `rag_query` call (query, answer, similarity, citations, token usage, retrieval/generation timings).

### Scrape pipeline (`scrapes/methods.py`)

`scrape_website(url, ...)` → `extract_url_content(...)` recursively. Depth-first with a shared `visited` set; default `max_depth=5`. For each page:

1. Fetch HTML via `requests` (default) or headless Chrome via Selenium (`--selenium`). Selenium uses `webdriver_manager` to install ChromeDriver and waits ~5s after `body` appears for JS to settle.
2. BeautifulSoup strips `script/style/nav/footer/header`, then prefers `<main>` → `<article>` → `<body>`.
3. Convert to markdown with `markdownify` (ATX headings).
4. If `--include-images`, image URLs are pulled out of the markdown into `Website.images` and removed from the content.
5. Same-domain links are extracted from the markdown and recursively followed. Link extraction handles absolute, root-relative, and relative paths, and explicitly filters out image extensions and `#`/`mailto:`/`tel:`/`javascript:` links.
6. Persists via `Website.objects.update_or_create(url=..., scrape=...)` so re-scrapes update in place.

`--save-json` additionally dumps the resulting `Website` rows to a JSON file in the project root (numeric suffix to avoid overwrites).

### Chunking (`scrapes/chunking.py`)

Markdown-aware, token-budgeted chunker — **not** word/char based. Uses `markdown-it-py` to walk block structure and `tiktoken` (encoding picked from `settings.RAG["EMBEDDING_MODEL"]`) for token counts.

- Extracts top-level blocks (heading/paragraph/fence/list/blockquote/…). Fenced code is atomic during packing.
- Blocks over budget fall back to sentence-split, then word-split.
- Packs blocks greedily up to `settings.RAG["CHUNK_TOKENS"]` (default 500). A new heading that arrives after body text ends the previous section.
- Each chunk carries a `heading_path` (the stack of enclosing headings).
- Prepends `settings.RAG["CHUNK_OVERLAP_TOKENS"]` (default 75) tokens decoded from the previous chunk as overlap.

### RAG pipeline (`scrapes/rag.py`)

Uses the OpenAI SDK. The client is **lazily constructed** on first use (not at import) — `OPENAI_API_KEY` must be present in `.env`, loaded by `core/settings.py` via `dotenv.load_dotenv(override=True)`. All OpenAI calls are wrapped with `tenacity` (5 tries, exponential backoff) against `RateLimitError`/`APIError`.

All tunables live in `settings.RAG` (in `core/settings.py`). Current values:

| Key | Value | Purpose |
|---|---|---|
| `EMBEDDING_MODEL` / `EMBEDDING_DIMS` | `text-embedding-3-small` / `1536` | embedding model; dims must match the `Chunk.embedding` column |
| `CHAT_MODEL` / `CHAT_TEMPERATURE` / `CHAT_MAX_TOKENS` | `gpt-4o-mini` / `0.2` / `800` | answer generation |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP_TOKENS` | `500` / `75` | chunker budget/overlap |
| `TOP_K` / `MIN_SIMILARITY` | `5` / `0.3` | retrieval output + generation gate |
| `EMBED_BATCH_SIZE` | `100` | embedding request batch size |
| `RETRIEVAL_OVERFETCH` | `4` | fetch `top_k * 4` from each retriever before fusion |
| `MAX_CHUNKS_PER_WEBSITE` | `2` | per-site diversity cap on final hits |
| `HNSW_EF_SEARCH` | `100` | per-transaction HNSW `ef_search` (latency/recall knob) |
| `FTS_CONFIG` | `simple` | Postgres text-search config for both indexing and queries |
| `HYBRID_RRF_K` | `60` | Reciprocal Rank Fusion constant |

Key functions:

- `index_website(website)` — chunks + embeds `website.content` and writes `Chunk` rows. **Idempotent**: skipped when `content_hash` and `indexed_with_model` both match the current content + embedding model. Deletes prior `Chunk` rows inside a transaction before bulk-inserting new ones, then populates each chunk's `search_vector` with a single `SearchVector("text", config=FTS_CONFIG)` update, and finally records the new `content_hash` / `indexed_with_model` on the `Website`.
- `semantic_search(query, top_k, scrape_id=None)` — **hybrid retrieval**. Runs a cosine-distance pgvector query and a Postgres `tsvector` FTS query (both limited to `top_k * RETRIEVAL_OVERFETCH`), fuses with Reciprocal Rank Fusion (`1 / (RRF_K + rank)`), backfills cosine similarity for FTS-only hits, applies the per-website diversity cap, and returns up to `top_k` hits. Each hit includes `similarity_score` (raw cosine). **`MIN_SIMILARITY` is not applied here** — it's a generation policy enforced in `rag_query`. Optional `scrape_id` restricts to one scrape.
- `rag_query(query, top_k, scrape_id=None)` — calls `semantic_search`, and if the top similarity is below `MIN_SIMILARITY` returns a "no relevant information" answer **without calling the chat model**. Otherwise builds a `[1] <url>\n<chunk>` context block and calls `CHAT_MODEL`. Parses `[N]` citations from the answer and reports any `invalid_citations` (numbers outside the source list). Always writes a `RAGQueryLog` row (best-effort; failures are logged and swallowed). Returns a dict with `answer`, `sources`, `below_threshold`, `top_similarity`, `invalid_citations`, `usage`, `model`, `retrieval_ms`, `generation_ms`.

Custom exceptions: `RAGError` → `RAGEmbeddingError`, `RAGGenerationError`. `index_websites` management command catches `RAGEmbeddingError` per-website and continues.

### Eval harness (`scrapes/eval/`)

`run_eval.py` loads query/expected-URL pairs from `queries.yaml`, normalizes URLs (lowercase scheme/host, strip trailing slash, drop fragment), runs each through `rag_query`, and writes `results/<phase>_<date>.json` with per-query hit positions, `recall@1`, `recall@k`, mean top-1 similarity, and tag-bucketed recall.

### Conventions to know

- Primary keys are **string ObjectIds**, not integers — don't assume `int` PKs in querysets, URL routing, admin code, or fixtures.
- Database is **Postgres** (defaults from env: `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5433`, `POSTGRES_DB=website_extractor`, `POSTGRES_USER=app`, `POSTGRES_PASSWORD=app`). The `vector` extension must be installed for pgvector migrations to apply. SQLite is no longer used.
- `.env` is git-ignored and loaded automatically. `OPENAI_API_KEY` plus the `POSTGRES_*` overrides are the relevant variables.
- The Django `SECRET_KEY` in `core/settings.py` is the default insecure dev key and `DEBUG=True`. Dev-only project.
- When editing retrieval or chunking, keep `index_website` idempotency in mind: changing the embedding model invalidates stored chunks automatically (via `indexed_with_model`), but changing the chunker without bumping something only re-indexes pages whose content also changed. Re-run `index_websites` explicitly if you change chunking logic.
