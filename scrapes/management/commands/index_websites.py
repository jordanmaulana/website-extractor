"""Management command to index websites for RAG."""

from django.core.management.base import BaseCommand
from scrapes.models import Website
from scrapes.rag import index_website


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

        for i, website in enumerate(websites, 1):
            try:
                index_website(website)
                self.stdout.write(
                    self.style.SUCCESS(f"✓ [{i}/{total}] Indexed: {website.url}")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f"✗ [{i}/{total}] Error indexing {website.url}: {e}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("\n✓ Indexing complete!"))
