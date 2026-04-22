from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from core.models import BaseModel


class Scrape(BaseModel):
    url = models.URLField(unique=True)


class Website(BaseModel):
    url = models.URLField()
    content = models.TextField()
    images = models.JSONField(default=list, blank=True)
    scrape = models.ForeignKey(Scrape, on_delete=models.CASCADE)
    content_hash = models.CharField(max_length=64, blank=True, default="")
    indexed_with_model = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = ["url", "scrape"]

    @property
    def is_indexed(self) -> bool:
        return bool(self.indexed_with_model)


class Chunk(BaseModel):
    website = models.ForeignKey(
        Website, on_delete=models.CASCADE, related_name="chunk_set"
    )
    chunk_index = models.PositiveIntegerField()
    text = models.TextField()
    token_count = models.PositiveIntegerField()
    heading_path = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=64)
    embedding = VectorField(dimensions=settings.RAG["EMBEDDING_DIMS"])
    search_vector = SearchVectorField(null=True)

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
            GinIndex(fields=["search_vector"], name="chunk_search_gin"),
        ]


class RAGQueryLog(BaseModel):
    """One row per `rag_query` call for audit and debugging."""

    query = models.TextField()
    answer = models.TextField(blank=True, default="")
    scrape_id = models.CharField(max_length=64, blank=True, default="")
    top_similarity = models.FloatField(null=True, blank=True)
    below_threshold = models.BooleanField(default=False)
    invalid_citations = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=64, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)
    retrieval_ms = models.FloatField(null=True, blank=True)
    generation_ms = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_on"]),
            models.Index(fields=["below_threshold"]),
        ]
