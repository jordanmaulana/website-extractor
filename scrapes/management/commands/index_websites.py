"""Management command to index websites for RAG."""

import logging

from django.core.management.base import BaseCommand
from scrapes.models import Website
from scrapes.rag import RAGEmbeddingError, index_website


log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Index all websites for RAG semantic search"

    def add_arguments(self, parser):
        parser.add_argument(
            "--scrape-id",
            type=str,
            help="Index only websites from a specific scrape ID",
        )

    def handle(self, *args, **options):
        if options["scrape_id"]:
            websites = Website.objects.filter(scrape_id=options["scrape_id"])
        else:
            websites = Website.objects.all()

        total = websites.count()
        self.stdout.write(f"Indexing {total} website(s)...")

        failures = 0
        for i, website in enumerate(websites, 1):
            try:
                index_website(website)
                self.stdout.write(
                    self.style.SUCCESS(f"✓ [{i}/{total}] Indexed: {website.url}")
                )
            except RAGEmbeddingError as e:
                failures += 1
                log.warning("Embedding failed for %s: %s", website.url, e)
                self.stdout.write(
                    self.style.WARNING(
                        f"⚠ [{i}/{total}] Embedding failed for {website.url}: {e}"
                    )
                )

        if failures:
            self.stdout.write(
                self.style.WARNING(f"\n✓ Indexing complete with {failures} failure(s).")
            )
        else:
            self.stdout.write(self.style.SUCCESS("\n✓ Indexing complete!"))
