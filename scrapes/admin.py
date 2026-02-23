from django.contrib import admin
from .models import Scrape, Website


# Register your models here.
@admin.register(Scrape)
class ScrapeAdmin(admin.ModelAdmin):
    list_display = ["id", "url", "created_on", "updated_on", "actor"]
    list_filter = ["created_on", "updated_on"]
    search_fields = ["url"]
    readonly_fields = ["id", "created_on", "updated_on"]


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
    list_display = ["id", "url", "scrape", "created_on", "updated_on", "actor"]
    list_filter = ["created_on", "updated_on", "scrape"]
    search_fields = ["url", "scrape__url"]
    readonly_fields = ["id", "created_on", "updated_on"]
