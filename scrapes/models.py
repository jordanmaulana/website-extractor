from django.db import models
from core.models import BaseModel


# Create your models here.
class Scrape(BaseModel):
    url = models.URLField()


class Website(BaseModel):
    url = models.URLField()
    content = models.TextField()
    scrape = models.ForeignKey(Scrape, on_delete=models.CASCADE)
