from django.db import models
from core.models import BaseModel


# Create your models here.
class Scrape(BaseModel):
    url = models.URLField(unique=True)


class Website(BaseModel):
    url = models.URLField()
    content = models.TextField()
    images = models.JSONField(default=list, blank=True)
    scrape = models.ForeignKey(Scrape, on_delete=models.CASCADE)
    chunks = models.JSONField(default=list, blank=True, help_text="Text chunks for RAG")
    embeddings = models.JSONField(
        default=list, blank=True, help_text="Embeddings for each chunk"
    )
    is_indexed = models.BooleanField(
        default=False, help_text="Whether content has been indexed for RAG"
    )

    class Meta:
        unique_together = ["url", "scrape"]
        indexes = [models.Index(fields=["is_indexed"])]
