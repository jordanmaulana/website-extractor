"""Tests for `scrapes.rag` behavior that does not require the database."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from openai import RateLimitError
from tenacity import wait_none


def _make_rate_limit_error() -> RateLimitError:
    """Construct a minimal RateLimitError (SDK requires an httpx.Response)."""
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx.Response(status_code=429, request=request)
    return RateLimitError(message="rate limited", response=response, body=None)


def test_rag_module_imports_without_api_key(monkeypatch):
    """The module must not construct the OpenAI client at import time."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import importlib

    from scrapes import rag

    importlib.reload(rag)
    assert rag._openai_client is None


def test_generate_embeddings_retries_on_rate_limit(monkeypatch):
    """Two RateLimitErrors then success: generate_embeddings returns, called 3×."""
    from scrapes import rag

    monkeypatch.setattr(rag._embed_batch.retry, "wait", wait_none())

    err = _make_rate_limit_error()
    success = MagicMock()
    success.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]

    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = [err, err, success]

    monkeypatch.setattr(rag, "_client", lambda: mock_client)

    result = rag.generate_embeddings(["hello world"])

    assert result == [[0.1, 0.2, 0.3]]
    assert mock_client.embeddings.create.call_count == 3


def test_generate_embeddings_exhausts_retries_and_raises_typed(monkeypatch):
    """After 5 failed attempts, the wrapper surfaces RAGEmbeddingError, not the SDK type."""
    from scrapes import rag

    monkeypatch.setattr(rag._embed_batch.retry, "wait", wait_none())

    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = _make_rate_limit_error()
    monkeypatch.setattr(rag, "_client", lambda: mock_client)

    with pytest.raises(rag.RAGEmbeddingError):
        rag.generate_embeddings(["x"])

    assert mock_client.embeddings.create.call_count == 5


def test_index_website_skips_when_content_hash_matches(monkeypatch):
    """Second call with unchanged content + matching model must not hit OpenAI."""
    import hashlib

    from django.conf import settings as dj_settings

    from scrapes import rag
    from scrapes.models import Website

    content = "Haji Furoda adalah haji khusus menggunakan visa undangan."
    website = Website(
        url="https://example.com/page",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        indexed_with_model=dj_settings.RAG["EMBEDDING_MODEL"],
    )

    mock_client = MagicMock()
    monkeypatch.setattr(rag, "_client", lambda: mock_client)

    rag.index_website(website)

    assert mock_client.embeddings.create.call_count == 0
