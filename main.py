#!/usr/bin/env python3
"""Extract URL content using Celery for async processing."""

import sys
import time

from tasks import extract_website_recursive, save_results_to_json


def main():
    """Main entry point that queues scraping tasks to Celery."""
    if len(sys.argv) < 2:
        url = input("Enter URL to extract: ").strip()
    else:
        url = sys.argv[1]

    if not url:
        print("Error: No URL provided")
        sys.exit(1)

    recursive = True
    if "--no-recursive" in sys.argv:
        recursive = False

    include_images = "--include-images" in sys.argv
    use_selenium = "--selenium" in sys.argv

    try:
        start_time = time.time()
        print(f"Extracting from: {url}")
        if use_selenium:
            print("  (Using headless browser for JavaScript rendering)")
        print("  (Using Celery for parallel processing)")
        print()

        # Queue the recursive extraction task
        max_depth = 5 if recursive else 0
        task = extract_website_recursive.delay(
            start_url=url,
            max_depth=max_depth,
            include_images=include_images,
            use_selenium=use_selenium,
        )

        print(f"Task queued: {task.id}")
        print("Waiting for completion...")
        print()

        # Wait for result
        results = task.get()

        # Save results
        save_task = save_results_to_json.delay(results, url)
        output_path = save_task.get()

        elapsed = time.time() - start_time
        print(f"\n✓ Saved {len(results)} page(s) to: {output_path}")
        print(f"⏱  Processing time: {elapsed:.2f}s")

    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
