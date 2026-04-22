"""RAG utilities for semantic search with website references."""

from __future__ import annotations

import hashlib
import logging
import re
import time

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection, transaction
from openai import APIError, OpenAI, RateLimitError
from pgvector.django import CosineDistance
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .chunking import chunk_markdown
from .models import Chunk, Website


log = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You answer questions using only the numbered sources provided in the user "
    "message. Cite sources inline as [1], [2], matching the numbered list. Only "
    "cite numbers that actually appear in the context. If the sources do not "
    "answer the question, say so plainly rather than guessing."
)

_CITATION_RE = re.compile(r"\[(\d+)\]")


class RAGError(Exception):
    """Base class for RAG errors."""


class RAGEmbeddingError(RAGError):
    """Raised when embedding generation fails."""


class RAGGenerationError(RAGError):
    """Raised when chat completion fails."""


_openai_client: OpenAI | None = None


def _client() -> OpenAI:
    """Construct the OpenAI client on first use and cache it."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, max=30),
    retry=retry_if_exception_type((RateLimitError, APIError)),
    reraise=True,
)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    response = _client().embeddings.create(
        model=settings.RAG["EMBEDDING_MODEL"], input=texts
    )
    return [item.embedding for item in response.data]


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings, batching by EMBED_BATCH_SIZE and retrying on rate limits."""
    batch_size = settings.RAG["EMBED_BATCH_SIZE"]
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), batch_size):
            out.extend(_embed_batch(texts[i : i + batch_size]))
    except (RateLimitError, APIError) as e:
        raise RAGEmbeddingError(str(e)) from e
    return out


def index_website(website: Website) -> None:
    """Chunk + embed a website's content, replacing any prior Chunk rows.

    No-op when content and embedding model match the last indexed state.
    """
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

    fts_config = settings.RAG["FTS_CONFIG"]
    with transaction.atomic():
        website.chunk_set.all().delete()
        Chunk.objects.bulk_create(
            [
                Chunk(
                    website=website,
                    chunk_index=i,
                    text=spec.text,
                    token_count=spec.token_count,
                    heading_path=spec.heading_path,
                    embedding_model=model,
                    embedding=vec,
                )
                for i, (spec, vec) in enumerate(zip(specs, embeddings))
            ]
        )
        Chunk.objects.filter(website=website).update(
            search_vector=SearchVector("text", config=fts_config)
        )
        website.content_hash = content_hash
        website.indexed_with_model = model
        website.save(update_fields=["content_hash", "indexed_with_model"])


def _set_hnsw_ef_search() -> None:
    """Tune HNSW ef_search for the current transaction — trades latency for recall."""
    ef_search = settings.RAG.get("HNSW_EF_SEARCH")
    if not ef_search:
        return
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL hnsw.ef_search = %s", [int(ef_search)])


def _apply_diversity_cap(
    ranked_chunks: list[Chunk],
    top_k: int,
    per_site_cap: int,
) -> list[Chunk]:
    """Keep at most ``per_site_cap`` chunks per website while preserving order."""
    out: list[Chunk] = []
    per_site: dict[str, int] = {}
    for chunk in ranked_chunks:
        count = per_site.get(chunk.website_id, 0)
        if count >= per_site_cap:
            continue
        per_site[chunk.website_id] = count + 1
        out.append(chunk)
        if len(out) >= top_k:
            break
    return out


def _format_hits(chunks: list[Chunk], similarities: dict[str, float]) -> list[dict]:
    return [
        {
            "website_url": chunk.website.url,
            "website_id": chunk.website_id,
            "chunk_id": chunk.id,
            "chunk": chunk.text,
            "heading_path": chunk.heading_path,
            "similarity_score": similarities.get(chunk.id, 0.0),
            "chunk_index": chunk.chunk_index,
        }
        for chunk in chunks
    ]


def semantic_search(
    query: str,
    top_k: int | None = None,
    scrape_id: str | None = None,
) -> list[dict]:
    """Hybrid vector + full-text search with Reciprocal Rank Fusion.

    Runs a cosine-distance vector query and a Postgres ``tsvector`` query in
    parallel, fuses results with RRF (``k = HYBRID_RRF_K``), then applies the
    per-website diversity cap. Returns up to ``top_k`` hits ranked by fused
    score, highest first. ``similarity_score`` on each hit is the raw cosine
    similarity so callers can still apply a threshold policy.

    MIN_SIMILARITY is NOT applied here — it's a generation policy, enforced by
    ``rag_query``.
    """
    top_k = top_k or settings.RAG["TOP_K"]
    model = settings.RAG["EMBEDDING_MODEL"]
    overfetch = settings.RAG["RETRIEVAL_OVERFETCH"]
    per_site_cap = settings.RAG["MAX_CHUNKS_PER_WEBSITE"]
    rrf_k = settings.RAG["HYBRID_RRF_K"]
    fts_config = settings.RAG["FTS_CONFIG"]
    fetch = top_k * overfetch

    query_vec = generate_embeddings([query])[0]

    _set_hnsw_ef_search()

    base_qs = Chunk.objects.filter(embedding_model=model)
    if scrape_id:
        base_qs = base_qs.filter(website__scrape_id=scrape_id)

    vec_hits = list(
        base_qs.alias(distance=CosineDistance("embedding", query_vec))
        .annotate(distance=CosineDistance("embedding", query_vec))
        .order_by("distance")[:fetch]
        .values_list("id", "distance")
    )

    fts_query = SearchQuery(query, config=fts_config)
    fts_hits = list(
        base_qs.annotate(rank=SearchRank("search_vector", fts_query))
        .filter(search_vector=fts_query)
        .order_by("-rank")
        .values_list("id", flat=True)[:fetch]
    )

    rrf: dict[str, float] = {}
    similarity_by_id: dict[str, float] = {}
    for rank, (cid, distance) in enumerate(vec_hits):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (rrf_k + rank)
        similarity_by_id[cid] = 1 - float(distance)
    for rank, cid in enumerate(fts_hits):
        rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (rrf_k + rank)

    if not rrf:
        return []

    fused_ids = sorted(rrf, key=rrf.get, reverse=True)
    hydrated = Chunk.objects.select_related("website").in_bulk(fused_ids)

    _fill_missing_similarities(hydrated, similarity_by_id, query_vec)

    ordered = [hydrated[cid] for cid in fused_ids if cid in hydrated]
    capped = _apply_diversity_cap(ordered, top_k, per_site_cap)
    return _format_hits(capped, similarity_by_id)


def _fill_missing_similarities(
    hydrated: dict[str, Chunk],
    similarity_by_id: dict[str, float],
    query_vec: list[float],
) -> None:
    """Compute cosine similarity for FTS-only hits that never got a vector score."""
    missing = [cid for cid in hydrated if cid not in similarity_by_id]
    if not missing:
        return
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32)
    q_norm = float(np.linalg.norm(q)) or 1.0
    for cid in missing:
        chunk = hydrated[cid]
        if chunk.embedding is None:
            similarity_by_id[cid] = 0.0
            continue
        e = np.asarray(chunk.embedding, dtype=np.float32)
        e_norm = float(np.linalg.norm(e)) or 1.0
        similarity_by_id[cid] = float(np.dot(q, e) / (q_norm * e_norm))


def rag_query(
    query: str,
    top_k: int | None = None,
    scrape_id: str | None = None,
) -> dict:
    """
    Query the RAG system and return answer with website references.

    Returns:
    {
        "query": str,
        "answer": str,
        "sources": [...],
        "below_threshold": bool,  # top similarity below MIN_SIMILARITY (no LLM call)
        "top_similarity": float | None,
        "invalid_citations": list[int],  # [N] citations referencing sources that don't exist
        "usage": {"prompt_tokens": int, ...} | None,
        "model": str | None,
        "retrieval_ms": float,
        "generation_ms": float | None,
    }
    """
    top_k = top_k or settings.RAG["TOP_K"]
    min_similarity = settings.RAG["MIN_SIMILARITY"]

    t0 = time.perf_counter()
    search_results = semantic_search(query, top_k=top_k, scrape_id=scrape_id)
    retrieval_ms = round((time.perf_counter() - t0) * 1000, 1)

    top_similarity = search_results[0]["similarity_score"] if search_results else None
    below_threshold = top_similarity is None or top_similarity < min_similarity

    sources = [
        {
            "website_url": r["website_url"],
            "website_id": r["website_id"],
            "chunk_id": r["chunk_id"],
            "chunk": r["chunk"],
            "heading_path": r["heading_path"],
            "similarity_score": r["similarity_score"],
        }
        for r in search_results
    ]

    if below_threshold:
        log.info(
            "rag_query below_threshold query=%r top_sim=%s min=%s sources=%d",
            query,
            top_similarity,
            min_similarity,
            len(search_results),
        )
        result = {
            "query": query,
            "answer": "No relevant information found in indexed websites.",
            "sources": sources,
            "below_threshold": True,
            "top_similarity": top_similarity,
            "invalid_citations": [],
            "usage": None,
            "model": None,
            "retrieval_ms": retrieval_ms,
            "generation_ms": None,
        }
        _persist_query_log(result, scrape_id)
        return result

    context = "\n\n".join(
        f"[{i + 1}] {r['website_url']}\n{r['chunk']}"
        for i, r in enumerate(search_results)
    )

    model = settings.RAG["CHAT_MODEL"]
    t1 = time.perf_counter()
    try:
        response = _client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Question: {query}\n\nSources:\n{context}",
                },
            ],
            temperature=settings.RAG["CHAT_TEMPERATURE"],
            max_tokens=settings.RAG["CHAT_MAX_TOKENS"],
        )
    except (RateLimitError, APIError) as e:
        raise RAGGenerationError(str(e)) from e
    generation_ms = round((time.perf_counter() - t1) * 1000, 1)

    answer = response.choices[0].message.content or ""
    usage = response.usage.model_dump() if response.usage else None

    cited = {int(n) for n in _CITATION_RE.findall(answer)}
    valid = set(range(1, len(search_results) + 1))
    invalid_citations = sorted(cited - valid)
    if invalid_citations:
        log.warning(
            "rag_query invalid citations query=%r cited=%s valid=%s",
            query,
            invalid_citations,
            sorted(valid),
        )

    result = {
        "query": query,
        "answer": answer,
        "sources": sources,
        "below_threshold": False,
        "top_similarity": top_similarity,
        "invalid_citations": invalid_citations,
        "usage": usage,
        "model": response.model,
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
    }
    _persist_query_log(result, scrape_id)
    return result


def _persist_query_log(result: dict, scrape_id: str | None) -> None:
    """Best-effort: persist a RAGQueryLog row for audit. Never raises to caller."""
    from .models import RAGQueryLog  # local import to avoid circular

    try:
        usage = result.get("usage") or {}
        RAGQueryLog.objects.create(
            query=result["query"],
            answer=result.get("answer") or "",
            scrape_id=scrape_id or "",
            top_similarity=result.get("top_similarity"),
            below_threshold=result.get("below_threshold", False),
            invalid_citations=result.get("invalid_citations") or [],
            sources=[
                {
                    "website_url": s["website_url"],
                    "website_id": s["website_id"],
                    "chunk_id": s["chunk_id"],
                    "similarity_score": s["similarity_score"],
                    "heading_path": s["heading_path"],
                }
                for s in result.get("sources", [])
            ],
            model=result.get("model") or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            retrieval_ms=result.get("retrieval_ms"),
            generation_ms=result.get("generation_ms"),
        )
    except Exception:  # noqa: BLE001 — logging must not break the caller
        log.exception("failed to persist RAGQueryLog")
