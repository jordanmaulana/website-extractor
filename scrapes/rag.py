"""RAG utilities for semantic search with website references."""

import numpy as np
from openai import OpenAI

from .models import Website


client = OpenAI()
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    words = text.split()

    current_chunk = []
    current_length = 0

    for word in words:
        word_length = len(word) + 1
        if current_length + word_length > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            overlap_words = int(overlap / 5)
            current_chunk = (
                current_chunk[-overlap_words:]
                if len(current_chunk) > overlap_words
                else current_chunk
            )
            current_length = sum(len(w) + 1 for w in current_chunk)

        current_chunk.append(word)
        current_length += word_length

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts using OpenAI."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def index_website(website: Website) -> None:
    """Index a website's content for RAG by creating chunks and embeddings."""
    if not website.content:
        return

    chunks = chunk_text(website.content)
    embeddings = generate_embeddings(chunks)

    website.chunks = chunks
    website.embeddings = embeddings
    website.is_indexed = True
    website.save()


def semantic_search(query: str, top_k: int = 5) -> list[dict]:
    """
    Perform semantic search across all indexed websites.

    Returns list of results with format:
    {
        "website_url": str,
        "website_id": str,
        "chunk": str,
        "similarity_score": float,
        "chunk_index": int
    }
    """
    query_embedding = generate_embeddings([query])[0]
    query_vector = np.array(query_embedding)

    results = []

    indexed_websites = Website.objects.filter(is_indexed=True)

    for website in indexed_websites:
        if not website.embeddings:
            continue

        embeddings_array = np.array(website.embeddings)
        similarities = np.dot(embeddings_array, query_vector) / (
            np.linalg.norm(embeddings_array, axis=1) * np.linalg.norm(query_vector)
        )

        top_indices = np.argsort(similarities)[::-1][:top_k]

        for idx in top_indices:
            if similarities[idx] > 0.5:
                results.append(
                    {
                        "website_url": website.url,
                        "website_id": website.id,
                        "chunk": website.chunks[idx],
                        "similarity_score": float(similarities[idx]),
                        "chunk_index": int(idx),
                    }
                )

    results.sort(key=lambda x: x["similarity_score"], reverse=True)
    return results[:top_k]


def rag_query(query: str, top_k: int = 5) -> dict:
    """
    Query the RAG system and return answer with website references.

    Returns:
    {
        "query": str,
        "answer": str,
        "sources": [
            {
                "website_url": str,
                "website_id": str,
                "chunk": str,
                "similarity_score": float
            }
        ]
    }
    """
    search_results = semantic_search(query, top_k=top_k)

    if not search_results:
        return {
            "query": query,
            "answer": "No relevant information found in indexed websites.",
            "sources": [],
        }

    context = "\n\n".join(
        [
            f"[Source {i + 1}: {result['website_url']}]\n{result['chunk']}"
            for i, result in enumerate(search_results)
        ]
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant that answers questions based on provided context. Always cite which sources you used.",
            },
            {
                "role": "user",
                "content": f"Based on the following context, answer this question: {query}\n\nContext:\n{context}",
            },
        ],
        temperature=0.7,
        max_tokens=500,
    )

    answer = response.choices[0].message.content

    return {
        "query": query,
        "answer": answer,
        "sources": [
            {
                "website_url": result["website_url"],
                "website_id": result["website_id"],
                "chunk": result["chunk"],
                "similarity_score": result["similarity_score"],
            }
            for result in search_results
        ],
    }
