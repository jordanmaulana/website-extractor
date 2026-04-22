# RAG Implementation Review

Review of `scrapes/rag.py` plus the `index_websites` / `rag_query` flow as of 2026-04-22.

## TL;DR

The current implementation is a working "hello world" RAG: chunk → embed with OpenAI → cosine similarity in NumPy → stuff top-K into a `gpt-4o-mini` prompt. It is fine for a few hundred pages on a developer laptop. It will fall over on three axes as soon as the corpus or traffic grows: (1) per-query memory & latency, because every indexed `Website.embeddings` JSON is deserialized and dot-producted on every query, (2) chunking quality, because the chunker counts characters but slices on words and ignores document structure, and (3) operational safety — no model versioning, no idempotency on re-index, no batching limits, no error handling around OpenAI calls.

The fix is not a rewrite. It is: store embeddings in a real vector index (sqlite-vec or pgvector), chunk on structure not whitespace, version the embedding model, and harden the OpenAI calls.

---

## Cons of the current implementation

### 1. Storage — embeddings live in a `JSONField`

`Website.embeddings` is a `JSONField(default=list)` holding a `list[list[float]]`. With `text-embedding-3-small` (1536 dims, 4 bytes each ≈ 6 KB per chunk **as floats** but stored as **JSON text**, so realistically 25–40 KB per chunk after JSON encoding), this means:

- `db.sqlite3` is already ~110 MB — most of that is JSON-encoded floats, which is the worst possible representation: you pay ~6× the binary size and you cannot index or search them inside the database.
- Every search loads the full `embeddings` array of every indexed `Website` into Python (`np.array(website.embeddings)` per row), parses JSON, allocates a NumPy matrix, then throws it away. This is O(N_websites × N_chunks_per_site) work on **every** query.
- There is no ANN index. Search is brute-force cosine similarity, in Python, in the web process.

**This is the single biggest problem.** It is also the easiest to fix.

### 2. Chunking — `chunk_text` is structurally blind

`chunk_text` (`scrapes/rag.py:15`) splits on whitespace, accumulates words until a **character** budget (`CHUNK_SIZE=500`) is hit, then keeps `overlap // 5` words for overlap (a hardcoded heuristic that the user must reverse-engineer). Issues:

- Mixes units: `chunk_size` is characters, `overlap` is characters but applied as words via `int(overlap / 5)`. The two parameters are not in the same coordinate system, so tuning one silently changes the other.
- Ignores Markdown structure even though `methods.py` produces clean Markdown. Headings, list items, code blocks, and tables get sliced mid-element. A heading like `## Lost luggage policy` can end up in a different chunk from the paragraph that explains it, which is exactly the kind of split that destroys retrieval quality.
- 500 characters is ~80–100 tokens. That is small enough that most chunks lose surrounding context; combined with a 10-word overlap, neighbouring chunks share almost nothing. Recommended baseline for `text-embedding-3-small` is 200–800 tokens with 10–20% overlap.
- No metadata is preserved per chunk: no source URL, no heading path, no position. The chunk is a bare string. All metadata has to be re-derived from the parent `Website` at query time.

### 3. Indexing — not idempotent, not incremental, not safe

`index_website` (`scrapes/rag.py:52`):

- Re-embeds the entire content every time it is called, even if `content` has not changed. There is no content hash or `embedding_model_version` to short-circuit.
- Writes `chunks`, `embeddings`, and flips `is_indexed=True` in three field assignments and one `.save()`. If the OpenAI call succeeds but `.save()` fails, the website is left un-indexed but billed. If the embedding call partially fails (rare but possible with very long inputs), there is no try/except and the management command crashes mid-batch.
- The management command `index_websites` iterates `Website.objects.all()` with no pagination, no `--only-unindexed` filter, and no concurrency. On a corpus of a few thousand pages this is one long serial run.
- `generate_embeddings` sends every chunk in a single `client.embeddings.create(... input=texts)` call with no batching. OpenAI's per-request token limit (300K tokens for embeddings) will be hit silently on long pages, raising an exception that aborts the whole command.

### 4. Retrieval — brute force, ignores the query, no filters

`semantic_search` (`scrapes/rag.py:66`):

- Loops over `Website.objects.filter(is_indexed=True)` and runs `np.dot` per row. There is no early termination, no pre-filtering, no caching of `np.linalg.norm(embeddings_array, axis=1)` (norms could be precomputed at index time and stored alongside).
- `top_k` is applied **per website** *and* globally. The code does `np.argsort(similarities)[::-1][:top_k]` per site, then re-sorts the union and slices `top_k` again. With many sites this returns a biased mix; with one site it returns up to `top_k` chunks from the same page, which often hurts answer diversity.
- The 0.5 cosine threshold is hardcoded and applied **before** the global top-K. For `text-embedding-3-small` on Indonesian / mixed-language content, 0.5 is a fairly tight cut. On short queries you can easily get zero results.
- No filtering by `Scrape`, language, recency, or URL pattern. Every query searches everything ever indexed, forever.

### 5. Generation — thin and brittle

`rag_query` (`scrapes/rag.py:113`):

- Hardcodes model (`gpt-4o-mini`), temperature (0.7), and `max_tokens=500`. No way to override per call. 0.7 is high for a citation-style QA system; 0.0–0.3 is more typical.
- The system prompt asks the model to "cite which sources you used" but the user message gives sources as `[Source 1: <url>]` headings without instructing the model how to cite. The output has no enforced citation format, so `result["sources"]` and the actual answer text can disagree silently.
- No prompt caching, no streaming, no tool-use scaffolding for follow-up questions. Every query pays full input-token cost.
- No handling for OpenAI errors (rate limits, 5xx, content filter). One blip 500s the request.
- No conversation memory — each call is independent. Fine for a CLI demo, a problem for a chat UI.

### 6. Operational gaps

- **No model versioning.** `EMBEDDING_MODEL` is a module constant. If you change it, every existing `embeddings` value is silently incompatible; cosine similarity will still "work" mathematically and return garbage. The `Website` row should record which model produced its embeddings, and search should refuse to mix.
- **No content fingerprinting.** Re-running `index_websites` re-embeds everything, even unchanged pages. Add a `content_hash` field and skip if unchanged.
- **`OpenAI()` is constructed at import time** in `scrapes/rag.py:9`. This means `import scrapes.rag` requires `OPENAI_API_KEY` to be set, even if you only want to read chunks. Move the client into a lazy accessor.
- **No tests.** `scrapes/tests.py` is empty. Chunk boundaries and similarity thresholds are exactly the kind of logic that needs unit tests.
- **No observability.** Token counts, latency, similarity score distributions — none are logged or persisted, so there is no way to diagnose why a particular query returned junk.
- **PII / cost controls.** Anything in `Website.content` is sent to OpenAI on every re-index and to `gpt-4o-mini` on every query. There is no allow-list, no redaction, no per-day spend cap.

---

## How it should be done

In rough order of impact-per-effort.

### A. Store embeddings in a real vector index (highest impact)

The project is already on SQLite. Two reasonable paths:

1. **`sqlite-vec`** — drop-in extension, single file, no new infra. Schema becomes:
   ```sql
   CREATE TABLE chunk (
     id INTEGER PRIMARY KEY,
     website_id TEXT REFERENCES scrapes_website(id),
     chunk_index INTEGER,
     text TEXT,
     content_hash TEXT,
     embedding_model TEXT,
     created_on DATETIME
   );
   CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[1536]);
   ```
   Search becomes a single SQL query that returns the top-K globally in milliseconds, no Python loop.

2. **Postgres + pgvector** — if the project will move off SQLite for production anyway (the 110 MB SQLite file and `gunicorn` + `whitenoise` deps suggest a deploy is planned), do it now. Same shape, plus HNSW indexing for sub-linear search at corpus scale.

Either way: split `Website.embeddings` (JSON list) into a real `Chunk` model, one row per chunk, with a foreign key back to `Website`. Drop the JSON field after backfill.

### B. Chunk on structure, not whitespace

Replace `chunk_text` with a Markdown-aware splitter:

- Split first on headings (`#`, `##`, ...) to get sections; within a section, split on paragraphs; only fall back to word-level splitting if a paragraph exceeds the budget.
- Use **tokens** as the unit (tiktoken with the embedding model's encoding), not characters. Target 400–600 tokens per chunk with ~15% overlap.
- Attach metadata to each chunk at index time: `{ "url", "heading_path": ["H1", "H2"], "position": 3, "content_hash": "..." }`. Store in the `Chunk` row.
- Strip boilerplate (cookie banners, "share this article", repeated nav fragments) before chunking — `methods.py` already removes `nav/footer/header`, but Markdown still tends to contain repeated link lists. A simple n-gram dedup across pages of the same `Scrape` removes most of it.

### C. Make indexing idempotent and incremental

- Add `Website.content_hash` (sha256 of `content`). In `index_website`, short-circuit if hash + model version match what's already stored.
- Add `--only-unindexed` and `--rebuild` flags to `index_websites`.
- Batch embedding requests in chunks of ~100 inputs (or by token budget) with retry + exponential backoff. The OpenAI SDK has `with_options(max_retries=...)`; use it.
- Wrap the per-website work in `transaction.atomic()` so a partial failure doesn't leave an inconsistent row.
- Log per-website: chunk count, total tokens, latency, model. A simple `IndexRun` model is enough.

### D. Improve retrieval

- Pre-compute and store `embedding_norm` per chunk so cosine becomes a single dot product.
- Apply the similarity threshold **after** taking the global top-K, not before — better to return weak results with a low-confidence flag than nothing.
- Add a `Scrape` (or domain) filter to `semantic_search` so callers can scope queries: "only answer from these sources."
- Consider hybrid search: combine vector similarity with BM25/FTS5 (SQLite has FTS5 built in). For Indonesian content with many proper nouns ("Uhud Tour", flight numbers, hotel names), keyword recall noticeably helps.
- Add an MMR or simple per-`website_id` cap to diversify the top-K so one long page can't monopolize results.

### E. Tighten generation

- Drop temperature to 0.2 for QA. Raise `max_tokens` to ~800 and stream the response.
- Move the system prompt into a constant and instruct an explicit citation format, e.g. `[1]`, `[2]` referencing a numbered source list. Validate post-hoc that every cited number exists.
- Use Anthropic-style **prompt caching** if you switch to Claude, or OpenAI's prompt caching for `gpt-4o-mini` — the system prompt and source preamble are stable across queries and should be cached.
- Wrap the chat call in retry/backoff with a typed exception (`RAGGenerationError`) so callers can distinguish "no sources" from "model failed."
- Return `usage` (input/output tokens) from `rag_query` so the caller can log cost.

### F. Operational hygiene

- Lazy-construct the OpenAI client (`def _client(): ...`) so `import scrapes.rag` doesn't require an API key.
- Add a `RAG_*` settings block in `core/settings.py`: model names, chunk size, top-K, threshold, cost cap. Read once, not as module constants.
- Persist a `RAGQuery` row per call (query text, top-K results, similarity scores, model, tokens, latency). This is the only way to debug "why did the bot say that?" later.
- Add unit tests for `chunk_text` (boundaries, overlap, Unicode), `semantic_search` (deterministic with a fake embedder), and `rag_query` (mocked OpenAI client).
- Wire a basic `/api/rag/query/` endpoint with `djangorestframework` (already in `pyproject.toml` but unused). Right now the only entry points are a CLI script and the Django shell.

---

## Suggested incremental path

1. **Week 1** — Add `Chunk` model + `content_hash` + `embedding_model` columns. Backfill from existing `Website.embeddings`. Keep the JSON field temporarily for rollback. Add tests.
2. **Week 2** — Replace the brute-force search with `sqlite-vec`. Delete the JSON `embeddings` column. Add per-`Scrape` filter and hybrid FTS5 keyword fallback.
3. **Week 3** — Markdown-aware chunker with tiktoken. Re-index. Compare retrieval quality on a small held-out query set.
4. **Week 4** — Harden generation: lower temperature, citation format, retries, usage logging, REST endpoint, basic spend cap.

Steps 1 and 2 alone remove ~80% of the long-term risk. Steps 3 and 4 are about answer quality and shipping it.
