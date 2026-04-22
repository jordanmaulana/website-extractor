# RAG Build Checklist

Implementation plan for the website-extractor RAG pipeline. Postgres + `pgvector` is the target from day one; full-text search uses Postgres `tsvector`.

**Project context:** early-stage, single developer, no users. Re-scrape and re-index freely when the schema or chunker changes.

Conventions:
- `[ ]` = todo, `[x]` = done.
- Each step names the file(s) touched so the work is concrete, not abstract.
- "Verify" steps describe how to prove the step worked before moving on.
- Any time you change the schema or the chunker: re-migrate, re-scrape, re-index. Don't write data-migration shims.

---

## Phase 0 — Postgres setup

### 0.1 Django wiring
- [x] `uv add "psycopg[binary]"` (psycopg3).
- [x] `core/settings.py` `DATABASES` block reads from `POSTGRES_*` env vars:
  ```python
  DATABASES = {
      "default": {
          "ENGINE": "django.db.backends.postgresql",
          "NAME": os.getenv("POSTGRES_DB", "website_extractor"),
          "USER": os.getenv("POSTGRES_USER", "app"),
          "PASSWORD": os.getenv("POSTGRES_PASSWORD", "app"),
          "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
          "PORT": os.getenv("POSTGRES_PORT", "5432"),
      }
  }
  ```
- [x] `.env` holds the same vars (gitignored).

### 0.2 Local database
- [x] Local Postgres instance with `vector` extension available (user-managed).
- [x] `make migrate` — `scrapes_scrape`, `scrapes_website`, `scrapes_chunk`, `core_appsetting`, auth tables all present; `pg_extension` lists `vector` v0.8.2.

### 0.3 First scrape
- [x] `make test` (runs `scrape_url uhudtour.com --include-images`). Produced 56 `Website` rows in ~18s; `strip_boilerplate` removed 2,117 duplicated nav/footer paragraphs across pages.

### 0.4 Docs
- [ ] Update `CLAUDE.md`: Postgres setup (env vars, `make db`/`make migrate` flow).
- [ ] Update `README.md` Development section to match (or delete it — it's stale).

**Phase 0 exit criteria:** ✅ `scrape_url` and `index_websites` run end-to-end against Postgres.

---

## Phase 1 — Eval scaffold (½ day, recommended)

Without this you'll have no way to tell whether a chunker or retrieval change made things better or worse. Even 10 hand-picked queries pay for themselves immediately.

- [x] **Eval set.** `scrapes/eval/queries.yaml` — 22 queries across both corpora, each tagged (`factual`, `conceptual`, `proper_noun`, `transactional`, `contact`, `navigational`) with the URL(s) that should appear in the top-K.
- [x] **Eval runner.** `scrapes/eval/run_eval.py` — for each query, calls `rag_query`, normalizes URLs, records hit positions, writes `scrapes/eval/results/<phase>_<date>.json` with recall@1 / recall@k / mean top-1 similarity / per-tag breakdown. Supports `--only id1,id2` and `--top-k N`.
- [x] **Captured the baseline.** `scrapes/eval/results/baseline_2026-04-22.json` — 22 queries over the uhudtour.com corpus only (sisi.id not yet scraped): recall@1 = recall@5 = **0.591** overall; **13 / 14** uhud queries hit at rank 1 (92.8%); mean top-1 similarity 0.580. All 8 sisi queries miss with similarities 0.27–0.40 (expected, unindexed). Every later phase diffs against this file.
- [x] **Test scaffolding.** `scrapes/tests/` package with `test_chunking.py`, `test_search.py`, `test_rag.py`. `pytest` + `pytest-django` + `pyyaml` in dev deps. `[tool.pytest.ini_options]` wires `DJANGO_SETTINGS_MODULE = "core.settings"`. `uv run pytest` runs.

---

## Phase 2 — RAG module hardening (1 day)

All code-only changes; safe to do in any order.

### 2.1 Lazy OpenAI client
- [x] In `scrapes/rag.py`, construct the client on first use via `_client()` and cache in a module global. `generate_embeddings` and `rag_query` call `_client()`.
- [x] **Verify:** `OPENAI_API_KEY= DJANGO_SETTINGS_MODULE=core.settings uv run python -c "import django; django.setup(); import scrapes.rag; assert scrapes.rag._openai_client is None"` succeeds.

### 2.2 Centralize config
- [x] `RAG` dict in `core/settings.py`:
  ```python
  RAG = {
      "EMBEDDING_MODEL": "text-embedding-3-small",
      "EMBEDDING_DIMS": 1536,
      "CHAT_MODEL": "gpt-4o-mini",
      "CHUNK_TOKENS": 500,
      "CHUNK_OVERLAP_TOKENS": 75,
      "TOP_K": 5,
      "MIN_SIMILARITY": 0.5,
      "CHAT_TEMPERATURE": 0.2,
      "CHAT_MAX_TOKENS": 800,
      "EMBED_BATCH_SIZE": 100,
  }
  ```
- [x] `scrapes/rag.py` reads every knob from `django.conf.settings.RAG`.
- [x] **Verify:** `grep "text-embedding\|gpt-4o" scrapes/rag.py` returns nothing.

### 2.3 Retries + batching for embeddings
- [x] `uv add tenacity`.
- [x] `_embed_batch` decorated with `@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=30), retry=retry_if_exception_type((RateLimitError, APIError)), reraise=True)`.
- [x] `generate_embeddings` batches in groups of `EMBED_BATCH_SIZE` and concatenates results in input order.
- [x] **Verify:** unit test mocks the OpenAI client to raise `RateLimitError` twice then succeed; asserts `embeddings.create.call_count == 3`.

### 2.4 Tighten generation
- [x] `rag_query` uses `settings.RAG["CHAT_TEMPERATURE"]` (0.2) and `CHAT_MAX_TOKENS` (800).
- [x] System prompt is a module constant `_SYSTEM_PROMPT` instructing `[1]`, `[2]` inline citations matching the numbered source list, only citing numbers that appear.
- [x] Sources formatted in the user message as `[1] <url>\n<chunk>\n\n[2] ...`.
- [x] Return dict includes `usage` (prompt/completion tokens) and `model`.
- [ ] **Verify:** after Phase 3 lands, run an eval query and confirm the answer contains `[N]`-style citations and the result dict has `usage`.

### 2.5 Typed errors
- [x] `RAGError`, `RAGEmbeddingError`, `RAGGenerationError` in `scrapes/rag.py`.
- [x] OpenAI calls wrapped so callers see typed exceptions.
- [x] `index_websites` catches `RAGEmbeddingError` per website, logs it, continues.

**Phase 2 exit criteria:** `make lint` clean, `uv run pytest` green.

---

## Phase 3 — Schema + chunker + pgvector (3–4 days)

Define the real schema from scratch: `Website` with indexing metadata, `Chunk` with a `vector` column + HNSW index, a Markdown-aware chunker.

### 3.1 Deps
- [x] `uv add pgvector tiktoken markdown-it-py`.

### 3.2 Enable the `vector` extension
- [x] Prepend `VectorExtension()` as the first operation in `scrapes/migrations/0001_initial.py` so the extension is created before `Chunk`'s `VectorField`:
  ```python
  from pgvector.django import VectorExtension
  operations = [VectorExtension(), migrations.CreateModel(name="Scrape", ...), ...]
  ```
- [ ] `make migrate`. **Verify:** `\dx` in psql lists `vector`.

### 3.3 Models
In `scrapes/models.py`:

- [x] `Website` fields: `url`, `content`, `images`, `scrape`, `content_hash: CharField(max_length=64, blank=True, default="")`, `indexed_with_model: CharField(max_length=64, blank=True, default="")`. Expose `is_indexed` as `@property` returning `bool(self.indexed_with_model)`.
- [x] `Chunk(BaseModel)`:
  ```python
  from pgvector.django import VectorField, HnswIndex

  class Chunk(BaseModel):
      website = models.ForeignKey(Website, on_delete=models.CASCADE, related_name="chunk_set")
      chunk_index = models.PositiveIntegerField()
      text = models.TextField()
      token_count = models.PositiveIntegerField()
      heading_path = models.JSONField(default=list, blank=True)
      embedding_model = models.CharField(max_length=64)
      embedding = VectorField(dimensions=1536)

      class Meta:
          unique_together = ["website", "chunk_index"]
          indexes = [
              models.Index(fields=["embedding_model"]),
              HnswIndex(
                  name="chunk_embedding_hnsw",
                  fields=["embedding"],
                  m=16,
                  ef_construction=64,
                  opclasses=["vector_cosine_ops"],
              ),
          ]
  ```
- [x] `makemigrations scrapes` regenerated `0001_initial.py` with `CreateModel(Scrape|Website|Chunk)`, `AddIndex(HnswIndex(...))`, and both `unique_together` constraints.
- [x] **Verify:** `scrapes_chunk` has `embedding | vector(1536)`; indexes include `chunk_embedding_hnsw` (`USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`) and the `embedding_model` btree. `vector` extension v0.8.2 is installed.

### 3.4 Markdown-aware chunker
- [x] New module `scrapes/chunking.py`. `ChunkSpec` dataclass (`text`, `token_count`, `heading_path`). `chunk_markdown(md, *, budget=None, overlap=None)` (overrides fall back to `settings.RAG["CHUNK_TOKENS"]` / `CHUNK_OVERLAP_TOKENS`).
- [x] Algorithm:
  1. Walks `markdown_it` token stream at `level==0`, extracts top-level blocks using `token.map` line ranges (paragraphs, headings, fenced code, lists, blockquotes).
  2. Maintains a `heading_path` stack updated on each heading token.
  3. Greedily packs atoms up to `CHUNK_TOKENS` tokens (tiktoken encoder for `EMBEDDING_MODEL`). A heading arriving after body text flushes the current chunk, so each chunk has exactly one `heading_path`. Fenced code stays atomic during packing.
  4. Any block exceeding the budget is pre-expanded via `_fallback_split`: sentence split on `(?<=[.!?])\s+`, then word-level if a sentence itself is oversize.
  5. Prepends `CHUNK_OVERLAP_TOKENS` decoded from the previous chunk's tail.
- [x] Tests in `scrapes/tests/test_chunking.py`: empty input, single short paragraph, oversize paragraph, multiple headings produce correct `heading_path`, oversize fenced code falls through, Indonesian Unicode round-trips cleanly.
- [x] **Verify:** `uv run pytest scrapes/tests/test_chunking.py` → 6/6 green.

### 3.5 `index_website`
- [x] In `scrapes/rag.py`:
  ```python
  from django.db import transaction
  from .chunking import chunk_markdown
  from .models import Chunk

  def index_website(website: Website) -> None:
      if not website.content:
          return
      content_hash = hashlib.sha256(website.content.encode()).hexdigest()
      model = settings.RAG["EMBEDDING_MODEL"]
      if website.content_hash == content_hash and website.indexed_with_model == model:
          return
      specs = chunk_markdown(website.content)
      if not specs:
          return
      embeddings = generate_embeddings([s.text for s in specs])
      with transaction.atomic():
          website.chunk_set.all().delete()
          Chunk.objects.bulk_create([
              Chunk(
                  website=website, chunk_index=i, text=spec.text,
                  token_count=spec.token_count, heading_path=spec.heading_path,
                  embedding_model=model, embedding=vec,
              )
              for i, (spec, vec) in enumerate(zip(specs, embeddings))
          ])
          website.content_hash = content_hash
          website.indexed_with_model = model
          website.save(update_fields=["content_hash", "indexed_with_model"])
  ```
- [x] **Verify (unit scope):** `test_index_website_skips_when_content_hash_matches` — an in-memory `Website` with matching `content_hash` + `indexed_with_model` short-circuits before any OpenAI call (`embeddings.create.call_count == 0`).
- [x] **Verify (integration):** `index_websites` over 56 websites populated 411 `Chunk` rows in a single pass and set `content_hash` + `indexed_with_model` on every `Website`; a re-run exits immediately (DB-level idempotency).

### 3.6 `semantic_search`
- [x] Pure Django ORM, no raw SQL (see `scrapes/rag.py`): `CosineDistance("embedding", query_vec)` with `.alias()` + `.annotate()`, over-fetching `top_k * 4`, per-website cap of 2 chunks, similarity = `1 - distance`, `select_related("website")`. Sources returned include `chunk_id` and `heading_path`.
- [x] `MIN_SIMILARITY` applied **after** the global top-K — falls back to unfiltered results when nothing meets the threshold.
- [x] **Verify:** with `enable_seqscan=off` + `enable_sort=off` the generated query plan is `Index Scan using chunk_embedding_hnsw on scrapes_chunk`. At 411 rows the planner naturally picks seq+sort (cost 91 vs 1058); HNSW will dominate as the corpus grows.

### 3.7 Boilerplate stripping
- [x] `strip_boilerplate(scrape, threshold=0.5, min_pages=5)` in `scrapes/methods.py`: splits each page on blank lines, counts paragraph frequency across all `Website` rows of the scrape, drops paragraphs appearing on more than 50% of pages. Called at the end of `scrape_website`; no-ops on scrapes smaller than `min_pages`.
- [x] Run on uhudtour.com scrape: **2,117 boilerplate paragraphs removed** across 56 pages (the recursive nav/footer that survived the BeautifulSoup pass).

### 3.8 Rescrape + reindex
- [x] `make migrate` against fresh Postgres.
- [x] `make test` → `uhudtour.com` (56 pages, 18s).
- [x] `uv run manage.py index_websites` → 411 chunks across 56 websites.
- [x] **Verify:** baseline eval captured — see Phase 1.

**Phase 3 exit criteria — all met:** `Chunk.objects.count() == 411`; `index_website` is a no-op on unchanged content; HNSW index confirmed in the plan; `baseline_2026-04-22.json` saved. `make lint` clean, `uv run pytest` → 10/10 green.

---

## Phase 4 — Hybrid search via Postgres FTS (1 day)

Vector search misses exact proper nouns (hotel names, flight numbers). Postgres `tsvector` with a GIN index catches them.

### 4.1 Search vector column
- [ ] In `scrapes/models.py`:
  ```python
  from django.contrib.postgres.search import SearchVectorField
  from django.contrib.postgres.indexes import GinIndex

  class Chunk(BaseModel):
      ...
      search_vector = SearchVectorField(null=True)

      class Meta:
          indexes = [
              ...,
              GinIndex(fields=["search_vector"], name="chunk_search_gin"),
          ]
  ```
- [ ] `make mmg && make migrate`.

### 4.2 Populate `search_vector` at index time
- [ ] In `index_website`, after `bulk_create`:
  ```python
  from django.contrib.postgres.search import SearchVector
  Chunk.objects.filter(website=website).update(
      search_vector=SearchVector("text", config="simple")
  )
  ```
  Use `'simple'` — Postgres has no Indonesian dictionary and `'simple'` does no stemming, which is fine for proper-noun recall. Switch per-language later if needed.
- [ ] **Verify:** `select text, search_vector from scrapes_chunk limit 1;` shows a non-null `tsvector`.

### 4.3 Hybrid search
- [ ] Run vector and FTS as two separate ORM queries, fuse with Reciprocal Rank Fusion (k=60):
  ```python
  from django.contrib.postgres.search import SearchQuery, SearchRank

  vec_hits = list(qs.alias(d=CosineDistance("embedding", query_vec)).order_by("d")[:top_k * 4].values_list("id", flat=True))
  fts_hits = list(
      qs.annotate(rank=SearchRank("search_vector", SearchQuery(query, config="simple")))
        .filter(rank__gt=0)
        .order_by("-rank")[:top_k * 4]
        .values_list("id", flat=True)
  )
  scores: dict[str, float] = {}
  for rank, cid in enumerate(vec_hits): scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
  for rank, cid in enumerate(fts_hits): scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
  fused_ids = sorted(scores, key=scores.get, reverse=True)
  ```
  Hydrate via `Chunk.objects.in_bulk(fused_ids)` and apply the per-website diversity cap.
- [ ] **Verify:** add 3–5 keyword-heavy queries (exact hotel names, place names) to the eval set; confirm they hit and semantic queries don't regress.

---

## Phase 5 — Generation polish + REST API (1–2 days)

### 5.1 Streaming
- [ ] `def rag_query_stream(query, top_k=None) -> Iterator[str]:` that yields tokens as they arrive (`stream=True`). Keep `rag_query` as a thin wrapper that consumes the stream.

### 5.2 Citation validation
- [ ] After generation: `cited = {int(m) for m in re.findall(r'\[(\d+)\]', answer)}`. If any cited number is outside `[1, len(sources)]`, log a warning and reprompt once: *"You cited source [N] but only sources [1..K] exist. Re-answer using only valid citations."*

### 5.3 REST endpoint
- [ ] `djangorestframework` is already in deps. Add `scrapes/serializers.py`, `scrapes/views.py`, register `path("api/rag/query/", RAGQueryView.as_view())` in `core/urls.py`.
- [ ] Endpoint accepts `{query, top_k?, scrape_id?}`, returns `{answer, sources, usage, latency_ms, query_id}`.
- [ ] `UserRateThrottle`. Auth: token or session.

### 5.4 Query log
- [ ] `RAGQuery(BaseModel)` in `scrapes/models.py`: `query`, `answer`, `top_k`, `sources` (JSON), `model`, `prompt_tokens`, `completion_tokens`, `latency_ms`. Inherits `actor` from `BaseModel`.
- [ ] Persist one row per `rag_query` call. This is the audit trail for "why did the bot say that."
- [ ] Register in `scrapes/admin.py` with sensible `list_display` / `search_fields`.

### 5.5 Prompt caching (optional)
- [ ] OpenAI auto-caches stable prefixes ≥ 1024 tokens. Structure messages so system prompt + source preamble come first, query last. Verify hit rate via `usage.prompt_tokens_details.cached_tokens`.
- [ ] If you switch to Claude, use `cache_control: {"type": "ephemeral"}` on the system block — see the `claude-api` skill.

---

## Phase 6 — Pre-production hygiene

Skip until there's a reason. Listed here so it's not forgotten.

- [ ] **Managed Postgres.** Replace the docker-compose db with a managed instance (Supabase, Neon, RDS, Fly Postgres). All support `pgvector`. Set connection vars in production env, not `.env`.
- [ ] **Connection pooling.** `CONN_MAX_AGE` in `DATABASES["default"]` or PgBouncer / managed equivalent. HNSW queries are cheap but connection setup is not.
- [ ] **Spend cap.** `RAG_DAILY_TOKEN_BUDGET` in settings. Sum `RAGQuery.prompt_tokens + completion_tokens` for today; refuse new queries with 429 if exceeded.
- [ ] **Public/private scope.** `Scrape.is_public` flag. Default search to `is_public=True`; require auth or explicit opt-in for private scrapes.
- [ ] **Redaction.** Regex emails / phone numbers / card-pattern strings out of content before sending to OpenAI. Log redaction counts.
- [ ] **Real secrets.** Replace the dev `SECRET_KEY` in `core/settings.py`, set `DEBUG=False`, populate `ALLOWED_HOSTS`. Move the secret key to env.
- [ ] **Backups.** Whatever managed Postgres you pick, confirm point-in-time recovery is on. The embeddings cost real money to regenerate.
- [ ] **Update `CLAUDE.md`** with: required env vars, Postgres connection setup, models in use, where the audit log lives, how to scope a query.
