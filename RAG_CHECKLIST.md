# RAG Rebuild Checklist

Step-by-step implementation plan based on `RAG_REVIEW.md`.

**Project context:** early-stage, single developer, no users. SQLite was a trial — the project is moving to **PostgreSQL** so it can use `pgvector` for the embeddings index and `tsvector` for full-text search. It is fine to wipe the database, drop columns without migration shims, and re-run `scrape_url` + `index_websites` to rebuild the corpus from scratch. The plan below assumes that freedom.

Conventions:
- `[ ]` = todo, `[x]` = done.
- Each step names the file(s) touched so the work is concrete, not abstract.
- "Verify" steps describe how to prove the step worked before moving on.
- Any time you change the schema or the chunker: drop the DB, re-migrate, re-scrape, re-index. Don't waste time writing migration data shims.

---

## Phase 0 — Migrate to PostgreSQL (½–1 day)

Prerequisite for Phase 3 (`pgvector`) and Phase 4 (Postgres FTS). Do this first.

### 0.2 Django wiring
- [ ] `uv add "psycopg[binary]"` (psycopg3, not the old psycopg2).
- [ ] In `core/settings.py`, replace the SQLite block with:
  ```python
  import os
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
- [ ] Add the same vars to `.env` (already gitignored).
- [ ] Drop the SQLite-specific `sqlite_vec` connection signal if you'd added one experimentally — pgvector loads as a Postgres extension via migration, no per-connection hook needed.

### 0.3 Wipe and re-migrate
- [ ] `rm db.sqlite3 db.sqlite3.bak* 2>/dev/null` and remove the gitignore entries that referenced SQLite (already excluded by `db.sqlite3*`, leave as-is).
- [ ] Delete every existing migration in `scrapes/migrations/` and `core/migrations/` except `__init__.py`. The schema is being regenerated against Postgres anyway and the old `bson.ObjectId` PK migration files have SQLite quirks not worth carrying over.
- [ ] `make db && make mmg && make migrate`.
- [ ] **Verify:** `make db-shell`, then `\dt` lists `scrapes_scrape`, `scrapes_website`, `core_appsetting`, plus the auth tables.

### 0.4 Re-scrape from scratch
- [ ] `make test` (the existing `scrape_url uhudtour.com --include-images` command).
- [ ] **Verify:** `select count(*) from scrapes_website;` is non-zero.

### 0.5 Update docs
- [ ] Update `CLAUDE.md`: replace the "SQLite, ~110 MB" section with Postgres setup (`make db` to start, env vars in `.env`, `docker compose down -v` to nuke).
- [ ] Drop the `db.sqlite3` references from `README.md`'s Development section (or just delete that section — the README is already stale).

**Phase 0 exit criteria:** `make dev` boots against Postgres; existing `scrape_url` and `index_websites` commands still work end-to-end (the legacy JSON-embedding code path still runs — Phase 3 will replace it).

---

## Phase 1 — Eval scaffold (½ day, optional but recommended)

You can skip this and code by feel. You'll regret it the first time you change the chunker and can't tell if retrieval got better or worse. Even 10 hand-picked queries pay for themselves immediately.

- [ ] **Pin a small evaluation set.** Create `scrapes/eval/queries.yaml` with 10–20 real questions, each with the URL(s) you'd consider a correct source. Indonesian queries are fine — match what you'll actually ask.
- [ ] **Write a baseline eval script.** `scrapes/eval/run_eval.py` — for each query, call `rag_query`, record top-K URLs and similarity scores, mark whether any expected URL appears. Save JSON to `scrapes/eval/results/<phase>_<date>.json`.
- [ ] **Capture the baseline.** Run it now against the post-Phase-0 implementation. Numbers to record: recall@1, recall@5, mean top-1 similarity. Every later phase compares against this.
- [ ] **Add test scaffolding.** Replace empty `scrapes/tests.py` with a `tests/` package (`__init__.py`, `test_chunking.py`, `test_search.py`, `test_rag.py`). Add `pytest` + `pytest-django` to dev deps. `uv run pytest` should run and find zero tests.

---

## Phase 2 — Operational hygiene (1 day, no schema changes)

Goal: fix the things that are wrong without touching the schema. All code-only changes; safe to do in any order.

### 2.1 Lazy OpenAI client
- [ ] In `scrapes/rag.py`, replace module-level `client = OpenAI()` with `def _client() -> OpenAI: ...` that constructs on first use and caches in a module global.
- [ ] Update `generate_embeddings` and `rag_query` to call `_client()`.
- [ ] **Verify:** `OPENAI_API_KEY= uv run python -c "import scrapes.rag"` succeeds.

### 2.2 Centralize config
- [ ] Add a `RAG` dict at the bottom of `core/settings.py`:
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
- [ ] Replace module constants in `scrapes/rag.py` with reads from `django.conf.settings.RAG`.
- [ ] **Verify:** `grep "text-embedding\|gpt-4o" scrapes/rag.py` returns nothing.

### 2.3 Retries + batching for embeddings
- [ ] `uv add tenacity`.
- [ ] Decorate `generate_embeddings` with `@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=30), retry=retry_if_exception_type((RateLimitError, APIError)))`.
- [ ] Batch inputs in groups of `EMBED_BATCH_SIZE` and concatenate results in input order.
- [ ] **Verify:** unit test mocks the OpenAI client to raise `RateLimitError` twice then succeed; assert the wrapper returns and was called 3×.

### 2.4 Tighten generation
- [ ] In `rag_query`: temperature → `settings.RAG["CHAT_TEMPERATURE"]`, `max_tokens` → 800.
- [ ] Move the system prompt to a module constant `_SYSTEM_PROMPT` and add: *"Cite sources inline as `[1]`, `[2]`, matching the numbered list. Only cite numbers that actually appear in the context."*
- [ ] Format sources in the user message as `[1] <url>\n<chunk>\n\n[2] ...` (drop the `[Source N: ...]` wrapper).
- [ ] Return `usage` (`prompt_tokens`, `completion_tokens`) and `model` in the result dict.
- [ ] **Verify:** run an eval query; answer contains `[1]`-style citations; result has `usage`.

### 2.5 Typed errors
- [ ] Define `RAGError`, `RAGEmbeddingError`, `RAGGenerationError` in `scrapes/rag.py`.
- [ ] Wrap OpenAI calls so callers see typed exceptions instead of raw SDK errors.
- [ ] In `index_websites`, catch `RAGEmbeddingError` per website, log it, and continue (don't abort the batch).

**Phase 2 exit criteria:** `make lint` clean, `uv run pytest` green, eval re-run shows the same numbers as Phase 1 (this phase only hardens; retrieval is unchanged).

---

## Phase 3 — Schema + chunker + pgvector, all in one (3–4 days)

Greenfield freedom: do the whole rewrite in one shot. Drop the JSON columns, add the `Chunk` model with a real `vector` column, ship the Markdown-aware chunker, and wire the HNSW index. Then re-scrape and re-index.

### 3.1 Add deps
- [ ] `uv add pgvector tiktoken markdown-it-py`.

### 3.2 Enable the `vector` extension
- [ ] Hand-write a migration `scrapes/migrations/000X_pgvector.py`:
  ```python
  from pgvector.django import VectorExtension
  class Migration(migrations.Migration):
      dependencies = [("scrapes", "<previous>")]
      operations = [VectorExtension()]
  ```
- [ ] `make migrate`. **Verify:** `make db-shell`, then `\dx` lists `vector`.

### 3.3 New models, drop the old ones
- [ ] In `scrapes/models.py`:
  - Strip `Website.chunks`, `Website.embeddings`, `Website.is_indexed` (and the `is_indexed` index).
  - Add `Website.content_hash = models.CharField(max_length=64, blank=True, default="")`.
  - Add `Website.indexed_with_model = models.CharField(max_length=64, blank=True, default="")`.
  - Add `@property def is_indexed(self) -> bool: return bool(self.indexed_with_model)`.
  - Add a new model:
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
  - The vector lives **on the `Chunk` row**. No virtual table, no separate join key.
- [ ] `make mmg && make migrate`. Inspect the generated migration first — confirm it has both `AddField('embedding', VectorField(...))` and `AddIndex(HnswIndex(...))`.
- [ ] **Verify:** `\d scrapes_chunk` in psql shows `embedding | vector(1536)`.

### 3.4 Markdown-aware chunker
- [ ] New module `scrapes/chunking.py`. Define `@dataclass class ChunkSpec: text: str; token_count: int; heading_path: list[str]` and `def chunk_markdown(md: str) -> list[ChunkSpec]:`.
- [ ] Algorithm:
  1. Split into blocks via `markdown_it` tokens (paragraphs, headings, fenced code, lists).
  2. Maintain a `heading_path` stack updated on each heading token.
  3. Greedily pack blocks into chunks up to `CHUNK_TOKENS` tokens (use `tiktoken.encoding_for_model("text-embedding-3-small")`). Never split a heading from the first block under it. Never split a fenced code block.
  4. If a single block exceeds the budget, fall back to sentence split (`re.split(r'(?<=[.!?])\s+', text)`), then word-level.
  5. Prepend `CHUNK_OVERLAP_TOKENS` worth of trailing tokens from the previous chunk.
- [ ] Tests in `tests/test_chunking.py`: empty input, single short paragraph, paragraph that exceeds budget, multiple headings produce correct `heading_path`, fenced code that exceeds budget falls through to sentence split, Indonesian Unicode round-trips cleanly.
- [ ] **Verify:** `uv run pytest tests/test_chunking.py` green.

### 3.5 Rewrite `index_website`
- [ ] In `scrapes/rag.py`:
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
      embeddings = generate_embeddings([s.text for s in specs])
      with transaction.atomic():
          website.chunk_set.all().delete()
          Chunk.objects.bulk_create([
              Chunk(
                  website=website, chunk_index=i, text=s.text,
                  token_count=s.token_count, heading_path=s.heading_path,
                  embedding_model=model, embedding=vec,
              )
              for i, (s, vec) in enumerate(zip(specs, embeddings))
          ])
          website.content_hash = content_hash
          website.indexed_with_model = model
          website.save(update_fields=["content_hash", "indexed_with_model"])
  ```
  Much simpler than the SQLite version — pgvector accepts the embedding directly via the `VectorField`, no packing or raw SQL.
- [ ] **Verify:** index one website twice — second call makes zero OpenAI requests (mock the client and assert `embeddings.create.call_count == 1` after two `index_website` calls).

### 3.6 Rewrite `semantic_search`
- [ ] Pure Django ORM, no raw SQL:
  ```python
  from pgvector.django import CosineDistance

  def semantic_search(query: str, top_k: int | None = None, scrape_id: str | None = None) -> list[dict]:
      top_k = top_k or settings.RAG["TOP_K"]
      query_vec = generate_embeddings([query])[0]
      qs = Chunk.objects.filter(embedding_model=settings.RAG["EMBEDDING_MODEL"])
      if scrape_id:
          qs = qs.filter(website__scrape_id=scrape_id)
      hits = (
          qs.alias(distance=CosineDistance("embedding", query_vec))
            .annotate(distance=CosineDistance("embedding", query_vec))
            .select_related("website")
            .order_by("distance")[: top_k * 4]  # over-fetch for diversity cap
      )
      results, seen = [], {}
      for chunk in hits:
          per_site = seen.get(chunk.website_id, 0)
          if per_site >= 2:
              continue
          seen[chunk.website_id] = per_site + 1
          similarity = 1 - float(chunk.distance)  # cosine distance → similarity
          results.append({
              "website_url": chunk.website.url, "website_id": chunk.website_id,
              "chunk_id": chunk.id, "chunk": chunk.text,
              "heading_path": chunk.heading_path, "similarity_score": similarity,
              "chunk_index": chunk.chunk_index,
          })
          if len(results) >= top_k:
              break
      filtered = [r for r in results if r["similarity_score"] >= settings.RAG["MIN_SIMILARITY"]]
      return filtered or results[:top_k]
  ```
- [ ] Apply `MIN_SIMILARITY` **after** the global top-K, not before — better to return weak results than nothing.
- [ ] **Verify:** Run `EXPLAIN` on the generated query in psql to confirm it uses the `chunk_embedding_hnsw` index (look for `Index Scan using chunk_embedding_hnsw`).

### 3.7 Boilerplate stripping
- [ ] In `scrapes/methods.py`, after `markdownify`: build a frequency map of paragraphs across all `Website` rows of the same `Scrape`, drop paragraphs that appear in >50% of pages (likely nav/footer leakage that survived the BeautifulSoup pass).
- [ ] Run on a small re-scrape and spot-check.

### 3.8 Wipe + rescrape + reindex
- [ ] `docker compose down -v && make db && make migrate` (nuclear reset of the DB volume).
- [ ] Re-run `make test` (the `scrape_url uhudtour.com` command) for each site you care about.
- [ ] `uv run manage.py index_websites`.
- [ ] **Verify:** re-run the eval set. Recall@5 should improve over the Phase 1 baseline (better chunker is the main lever). Search latency for the vector step should be sub-millisecond against HNSW.

**Phase 3 exit criteria:** `Chunk.objects.count() > 0`; `index_websites` is no-op on unchanged content; `EXPLAIN` confirms HNSW index usage; eval recall is up.

---

## Phase 4 — Hybrid search via Postgres FTS (1 day)

Vector search misses exact proper nouns (hotel names, flight numbers). Postgres `tsvector` with a GIN index catches them.

### 4.1 Add a search vector column
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
- [ ] In `index_website`, after `bulk_create`, populate via a single SQL update:
  ```python
  from django.contrib.postgres.search import SearchVector
  Chunk.objects.filter(website=website).update(
      search_vector=SearchVector("text", config="simple")
  )
  ```
  Use `'simple'` config — Postgres has no Indonesian dictionary and `'simple'` does no stemming, which is fine for proper-noun recall. If you later want stemming for English content, switch to `'english'` or detect language per-website.
- [ ] **Verify:** `select text, search_vector from scrapes_chunk limit 1;` shows a non-null `tsvector`.

### 4.3 Hybrid search in `semantic_search`
- [ ] Run vector and FTS as two separate ORM queries, then fuse with Reciprocal Rank Fusion:
  ```python
  from django.contrib.postgres.search import SearchQuery, SearchRank

  vec_hits = list(qs.alias(d=CosineDistance("embedding", query_vec)).order_by("d")[:top_k * 4].values_list("id", flat=True))
  fts_hits = list(
      qs.annotate(rank=SearchRank("search_vector", SearchQuery(query, config="simple")))
        .filter(rank__gt=0)
        .order_by("-rank")[:top_k * 4]
        .values_list("id", flat=True)
  )
  # RRF fusion
  scores: dict[str, float] = {}
  for rank, cid in enumerate(vec_hits): scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
  for rank, cid in enumerate(fts_hits): scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
  fused_ids = sorted(scores, key=scores.get, reverse=True)
  ```
  Then hydrate via `Chunk.objects.in_bulk(fused_ids)` and apply the per-website diversity cap.
- [ ] **Verify:** add 3–5 keyword-heavy queries to the eval set (exact hotel names, place names) and confirm they now hit; semantic queries should not regress.

---

## Phase 5 — Generation polish + REST API (1–2 days)

### 5.1 Streaming
- [ ] Add `def rag_query_stream(query, top_k=None) -> Iterator[str]:` that yields tokens as they arrive (`stream=True`). Keep `rag_query` as a thin wrapper that consumes the stream.

### 5.2 Citation validation
- [ ] After generation: `cited = {int(m) for m in re.findall(r'\[(\d+)\]', answer)}`. If any cited number is outside `[1, len(sources)]`, log a warning and re-prompt once: *"You cited source [N] but only sources [1..K] exist. Re-answer using only valid citations."*

### 5.3 REST endpoint
- [ ] `djangorestframework` is already in deps and unused. Add `scrapes/serializers.py`, `scrapes/views.py`, register `path("api/rag/query/", RAGQueryView.as_view())` in `core/urls.py`.
- [ ] Endpoint accepts `{query, top_k?, scrape_id?}`, returns `{answer, sources, usage, latency_ms, query_id}`.
- [ ] Add `UserRateThrottle`. Auth: token or session.

### 5.4 Query log
- [ ] Add `RAGQuery(BaseModel)` to `scrapes/models.py`: `query`, `answer`, `top_k`, `sources` (JSON), `model`, `prompt_tokens`, `completion_tokens`, `latency_ms`. Inherits `actor` from `BaseModel`.
- [ ] Persist one row per `rag_query` call. This is your only audit trail for "why did the bot say that."
- [ ] Register in `scrapes/admin.py` with sensible `list_display` / `search_fields`.

### 5.5 Prompt caching (optional, do when token cost matters)
- [ ] OpenAI auto-caches stable prefixes ≥ 1024 tokens. Structure messages so the system prompt + source preamble come first, query last. Verify hit rate via `usage.prompt_tokens_details.cached_tokens`.
- [ ] If you switch to Claude later, use `cache_control: {"type": "ephemeral"}` on the system block — see the `claude-api` skill.

---

## Phase 6 — Pre-production hygiene (when you start letting other people use this)

Skip until there's a reason. Listed here so it's not forgotten.

- [ ] **Managed Postgres.** Replace the docker-compose db with a managed instance (Supabase, Neon, RDS, Fly Postgres). All of these support `pgvector`. Set the connection vars in production env, not `.env`.
- [ ] **Connection pooling.** Add `CONN_MAX_AGE` to `DATABASES["default"]` (or use PgBouncer / the managed equivalent). HNSW queries are cheap but connection setup is not.
- [ ] **Spend cap.** `RAG_DAILY_TOKEN_BUDGET` in settings. Sum `RAGQuery.prompt_tokens + completion_tokens` for today; refuse new queries with 429 if exceeded.
- [ ] **Public/private scope.** `Scrape.is_public` flag. Default search to `is_public=True`; require auth or explicit opt-in for private scrapes.
- [ ] **Redaction.** Regex emails / phone numbers / card-pattern strings out of content before sending to OpenAI. Log redaction counts.
- [ ] **Real secrets.** Replace the dev `SECRET_KEY` in `core/settings.py`, set `DEBUG=False`, populate `ALLOWED_HOSTS`. Move the secret key to env.
- [ ] **Backups.** Whatever managed Postgres you pick, confirm point-in-time recovery is on. The embeddings cost real money to regenerate.
- [ ] **Update `CLAUDE.md`** with: required env vars, Postgres connection setup, models in use, where the audit log lives, how to scope a query.

---

## Quick wins if you only have one afternoon

In order — each is independently valuable. Only #1 requires Phase 0 to be done first.

1. **Phase 0 (minimum)** — start Postgres in docker, switch the Django DATABASES block, re-scrape one site. ~1 hour. Unblocks everything else.
2. **Phase 2.1 + 2.2** — lazy client, centralize config. ~1 hour, zero risk, schema-independent (works pre- or post-Phase-0).
3. **Phase 2.4** — drop temperature, fix citation format. ~30 min, immediate answer-quality bump.
4. **Phase 3.5 (just the `content_hash` skip)** — even before the full schema rewrite, add `content_hash` to `Website` and short-circuit `index_website` if it matches. ~1 hour, stops paying to re-embed unchanged pages.
5. **Phase 3.4 (Markdown chunker only, against the existing JSON storage)** — biggest single retrieval-quality lever; can be wired into the current `index_website` without touching the schema. ~½ day.

The real payoff is finishing Phase 3 in full — once `pgvector` + the `Chunk` model are in place, every later improvement is cheap.
