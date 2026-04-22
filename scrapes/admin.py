from django.contrib import admin
from .models import Chunk, RAGQueryLog, Scrape, Website


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


@admin.register(RAGQueryLog)
class RAGQueryLogAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "query",
        "top_similarity",
        "below_threshold",
        "model",
        "total_tokens",
        "retrieval_ms",
        "generation_ms",
        "created_on",
    ]
    list_filter = ["below_threshold", "model", "created_on"]
    search_fields = ["query", "answer", "scrape_id"]
    readonly_fields = [
        "id",
        "created_on",
        "updated_on",
        "query",
        "answer",
        "scrape_id",
        "top_similarity",
        "below_threshold",
        "invalid_citations",
        "sources",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "retrieval_ms",
        "generation_ms",
    ]
