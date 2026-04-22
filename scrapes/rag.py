"""RAG utilities for semantic search with website references."""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.db import transaction
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
        website.content_hash = content_hash
        website.indexed_with_model = model
        website.save(update_fields=["content_hash", "indexed_with_model"])


def semantic_search(
    query: str,
    top_k: int | None = None,
    scrape_id: str | None = None,
) -> list[dict]:
    """Cosine-distance search over Chunk.embedding with a per-website diversity cap."""
    top_k = top_k or settings.RAG["TOP_K"]
    model = settings.RAG["EMBEDDING_MODEL"]

    query_vec = generate_embeddings([query])[0]

    qs = Chunk.objects.filter(embedding_model=model)
    if scrape_id:
        qs = qs.filter(website__scrape_id=scrape_id)

    hits = (
        qs.alias(distance=CosineDistance("embedding", query_vec))
        .annotate(distance=CosineDistance("embedding", query_vec))
        .select_related("website")
        .order_by("distance")[: top_k * 4]
    )

    results: list[dict] = []
    per_site: dict[str, int] = {}
    for chunk in hits:
        count = per_site.get(chunk.website_id, 0)
        if count >= 2:
            continue
        per_site[chunk.website_id] = count + 1
        similarity = 1 - float(chunk.distance)
        results.append(
            {
                "website_url": chunk.website.url,
                "website_id": chunk.website_id,
                "chunk_id": chunk.id,
                "chunk": chunk.text,
                "heading_path": chunk.heading_path,
                "similarity_score": similarity,
                "chunk_index": chunk.chunk_index,
            }
        )
        if len(results) >= top_k:
            break

    min_sim = settings.RAG["MIN_SIMILARITY"]
    filtered = [r for r in results if r["similarity_score"] >= min_sim]
    return filtered or results[:top_k]


def rag_query(query: str, top_k: int | None = None) -> dict:
    """
    Query the RAG system and return answer with website references.

    Returns:
    {
        "query": str,
        "answer": str,
        "sources": [...],
        "usage": {"prompt_tokens": int, "completion_tokens": int, ...} | None,
        "model": str | None,
    }
    """
    top_k = top_k or settings.RAG["TOP_K"]
    search_results = semantic_search(query, top_k=top_k)

    if not search_results:
        return {
            "query": query,
            "answer": "No relevant information found in indexed websites.",
            "sources": [],
            "usage": None,
            "model": None,
        }

    context = "\n\n".join(
        f"[{i + 1}] {r['website_url']}\n{r['chunk']}"
        for i, r in enumerate(search_results)
    )

    model = settings.RAG["CHAT_MODEL"]
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

    answer = response.choices[0].message.content
    usage = response.usage.model_dump() if response.usage else None

    return {
        "query": query,
        "answer": answer,
        "sources": [
            {
                "website_url": r["website_url"],
                "website_id": r["website_id"],
                "chunk_id": r["chunk_id"],
                "chunk": r["chunk"],
                "heading_path": r["heading_path"],
                "similarity_score": r["similarity_score"],
            }
            for r in search_results
        ],
        "usage": usage,
        "model": response.model,
    }
