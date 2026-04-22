# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency / environment management uses `uv`. Most workflows go through the Makefile:

- `make dev` — run the Django dev server on port 8000 (`uv run manage.py runserver 8000`)
- `make lint` — `ruff format .` then `ruff check . --fix` (a Windsurf rule requires running this after every code change; resolve all errors before stopping)
- `make mmg` / `make migrate` — `makemigrations` / `migrate`
- `make upgrade` — `uv sync && uv lock --upgrade && uv sync --frozen --no-install-project`
- `make tw` — Tailwind watcher (`npx @tailwindcss/cli -i ./static/input.css -o ./static/output.css --watch`)
- `make test` — runs the `scrape_url` management command against `https://uhudtour.com/ --include-images` (this is a live scrape, not a unit test; there is no real test suite)

Running scrapes / RAG manually:

- Scrape a URL: `uv run manage.py scrape_url <url> [--no-recursive] [--max-depth N] [--include-images] [--selenium] [--save-json]`
- Index scraped content for RAG: `uv run manage.py index_websites [--scrape-id <id>]`
- Example RAG query script: `uv run python example_rag_query.py` (sets up Django, then calls `scrapes.rag.rag_query`)

The README still references a standalone `main.py` CLI — that entry point no longer exists. Use the Django management commands above instead.

## Architecture

This is a **Django 5.2 project** (`core/` is the project package) that wraps a website scraper plus a small RAG pipeline. There is no real frontend or REST routing yet — `core/urls.py` only mounts `/admin/`. Work happens through management commands and the Django admin.

### Apps

- **`core/`** — Django project + shared models. `core/models.py` defines `BaseModel` (abstract), which every domain model inherits. `BaseModel` uses a **string `bson.ObjectId` as primary key** (via `make_object_id`), plus `created_on`, `updated_on`, and an optional `actor` FK to `User`. Default ordering is by `id`. Also defines `AppSetting`, a typed key/value store with a `.get(key, value_type, default)` helper.
- **`scrapes/`** — the scraper + RAG. Two models in `scrapes/models.py`:
  - `Scrape` — one row per top-level URL the user asked to scrape (unique on `url`).
  - `Website` — one row per page discovered under a `Scrape` (unique together on `(url, scrape)`). Holds extracted markdown `content`, an `images` JSON list, plus RAG state: `chunks` (text chunks), `embeddings` (per-chunk vectors), and the `is_indexed` flag.

### Scrape pipeline (`scrapes/methods.py`)

`scrape_website(url, ...)` → `extract_url_content(...)` recursively. The recursion is in-process and depth-first with a shared `visited` set; default `max_depth=5`. For each page:

1. Fetch HTML via `requests` (default) or headless Chrome via Selenium (`--selenium`). Selenium uses `webdriver_manager` to install ChromeDriver and waits ~5s after `body` appears for JS to settle.
2. BeautifulSoup strips `script/style/nav/footer/header`, then prefers `<main>` → `<article>` → `<body>`.
3. Convert to markdown with `markdownify` (ATX headings).
4. If `--include-images`, image URLs are pulled out of the markdown into `Website.images` and removed from the content.
5. Same-domain links are extracted from the markdown and recursively followed. Link extraction handles absolute URLs, root-relative, and relative paths, and explicitly filters out image extensions and `#`/`mailto:`/`tel:`/`javascript:` links.
6. Persists via `Website.objects.update_or_create(url=..., scrape=...)` so re-scrapes update in place.

`--save-json` is a backwards-compat path that also dumps the resulting `Website` rows to a JSON file in the project root, with a counter suffix to avoid overwrites.

### RAG pipeline (`scrapes/rag.py`)

Uses the OpenAI SDK (`OpenAI()` is constructed at import — requires `OPENAI_API_KEY` in `.env`, which `core/settings.py` loads via `dotenv.load_dotenv(override=True)`).

- `chunk_text` — word-based chunking, default `CHUNK_SIZE=500` chars with `CHUNK_OVERLAP=50` (overlap is converted to "approx 10 words" via `overlap // 5`).
- `generate_embeddings` — single batched call to `text-embedding-3-small`.
- `index_website(website)` — chunks + embeds `website.content`, writes `chunks`, `embeddings`, sets `is_indexed=True`.
- `semantic_search(query, top_k)` — loads **all** indexed `Website` rows into memory, computes cosine similarity per row using NumPy, filters at `>0.5`, returns the global top-K. There is no vector DB; this scales linearly with corpus size.
- `rag_query(query, top_k)` — runs `semantic_search`, builds a `[Source N: url]` context block, and asks `gpt-4o-mini` (temp 0.7, max 500 tokens) to answer with citations.

### Conventions to know

- Primary keys are **string ObjectIds**, not integers — don't assume `int` PKs in querysets, URL routing, or admin code.
- `.env` is git-ignored and loaded automatically by `core/settings.py`. `OPENAI_API_KEY` is the main expected variable.
- `db.sqlite3` is committed-ignored but currently present in the working tree (~110 MB) and holds real scraped + indexed data — don't delete it casually.
- The Django `SECRET_KEY` in `core/settings.py` is the default insecure dev key and `DEBUG=True`. Treat the project as dev-only for now.
