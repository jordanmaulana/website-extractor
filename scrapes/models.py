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

    class Meta:
        unique_together = ["url", "scrape"]
