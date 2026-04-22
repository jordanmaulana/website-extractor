from django.contrib import admin
from .models import Chunk, Scrape, Website


@admin.register(Scrape)
class ScrapeAdmin(admin.ModelAdmin):
    list_display = ["id", "url", "created_on", "updated_on", "actor"]
    list_filter = ["created_on", "updated_on"]
    search_fields = ["url"]
    readonly_fields = ["id", "created_on", "updated_on"]


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "url",
        "scrape",
        "indexed_with_model",
        "created_on",
        "updated_on",
        "actor",
    ]
    list_filter = ["created_on", "updated_on", "scrape", "indexed_with_model"]
    search_fields = ["url", "scrape__url"]
    readonly_fields = ["id", "created_on", "updated_on", "content_hash"]


@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "website",
        "chunk_index",
        "token_count",
        "embedding_model",
        "created_on",
    ]
    list_filter = ["embedding_model", "created_on"]
    search_fields = ["website__url", "text"]
    readonly_fields = [
        "id",
        "created_on",
        "updated_on",
        "website",
        "chunk_index",
        "text",
        "token_count",
        "heading_path",
        "embedding_model",
    ]
