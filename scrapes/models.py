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
