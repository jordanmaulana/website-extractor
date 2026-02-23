"""Management command to scrape a URL and store results in the database."""

from django.core.management.base import BaseCommand, CommandError

from scrapes.methods import scrape_website


class Command(BaseCommand):
    help = "Scrape a URL and save results to the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "url",
            type=str,
            nargs="?",
            help="URL to scrape",
        )
        parser.add_argument(
            "--no-recursive",
            action="store_true",
            help="Disable recursive scraping of linked URLs",
        )
        parser.add_argument(
            "--max-depth",
            type=int,
            default=5,
            help="Maximum depth for recursive scraping (default: 5)",
        )
        parser.add_argument(
            "--include-images",
            action="store_true",
            help="Extract and count images from the content",
        )
        parser.add_argument(
            "--selenium",
            action="store_true",
            help="Use Selenium/headless browser for JavaScript rendering",
        )
        parser.add_argument(
            "--save-json",
            action="store_true",
            help="Also save results to a JSON file (for backward compatibility)",
        )

    def handle(self, *args, **options):
        url = options.get("url")

        # If no URL provided via argument, prompt interactively
        if not url:
            url = input("Enter URL to extract: ").strip()

        if not url:
            raise CommandError("Error: No URL provided")

        recursive = not options.get("no_recursive")
        max_depth = options.get("max_depth")
        include_images = options.get("include_images")
        use_selenium = options.get("selenium")
        save_json = options.get("save_json")

        try:
            scrape = scrape_website(
                url=url,
                recursive=recursive,
                max_depth=max_depth,
                include_images=include_images,
                use_selenium=use_selenium,
                save_json=save_json,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully created Scrape #{scrape.id} for {url}"
                )
            )

        except Exception as e:
            raise CommandError(f"Error scraping URL: {e}")
