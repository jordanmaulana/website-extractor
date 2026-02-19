#!/usr/bin/env python3
"""Extract URL content and save to JSON file."""

import json
import sys
import re
from urllib.parse import urlparse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md


def sanitize_filename(url: str) -> str:
    """Create a safe filename from URL."""
    parsed = urlparse(url)
    name = f"{parsed.hostname or 'unknown'}{parsed.path}"
    name = re.sub(r"[^\w\-_.]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if len(name) > 100:
        name = name[:100]
    return name or "extracted_content"


def extract_urls_from_markdown(markdown_content: str, base_url: str) -> set[str]:
    """Extract HTTP URLs from markdown content, filtering to only same-domain URLs."""
    urls = set()
    base_netloc = urlparse(base_url).netloc.lower()
    # Common image extensions to ignore
    image_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".ico",
        ".tiff",
        ".avif",
    )

    def is_image_url(url: str) -> bool:
        """Check if URL is an image file."""
        url_lower = url.lower().split("?")[0]  # Remove query params
        return url_lower.endswith(image_extensions)

    # Match markdown links [text](url)
    md_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", markdown_content)
    for _, url in md_links:
        url = url.strip()
        if is_image_url(url):
            continue
        if url.startswith(("http://", "https://")):
            if base_netloc in urlparse(url).netloc.lower():
                urls.add(url)
        elif url.startswith("/"):
            # Relative URL - join with base
            parsed_base = urlparse(base_url)
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
            if not is_image_url(full_url):
                urls.add(full_url)

    # Match bare URLs
    bare_urls = re.findall(r"https?://[^\s\)\]\>\"\']+", markdown_content)
    for url in bare_urls:
        if is_image_url(url):
            continue
        if base_netloc in urlparse(url).netloc.lower():
            urls.add(url)

    return urls


def extract_url_content(
    url: str,
    results: list[dict],
    visited: set[str],
    recursive: bool = True,
    depth: int = 0,
    max_depth: int = 2,
) -> None:
    """Fetch URL content and add to results list. Recursively extract linked URLs."""
    # Skip if already visited
    if url in visited:
        return
    visited.add(url)

    if not url.startswith(("http://", "https://")):
        return

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
        }
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        main_content = soup.find("main") or soup.find("article") or soup.find("body")
        if not main_content:
            main_content = soup

        html_content = str(main_content)
        markdown_content = md(html_content, heading_style="ATX")

        lines = markdown_content.split("\n")
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped or cleaned_lines:
                cleaned_lines.append(line)
        markdown_content = "\n".join(cleaned_lines).strip()

        # Add to results
        results.append({"url": url, "content": markdown_content})
        print(f"  ✓ Extracted: {url}")

        # Recursively extract linked URLs
        if recursive and depth < max_depth:
            linked_urls = extract_urls_from_markdown(markdown_content, url)
            for linked_url in linked_urls:
                if linked_url not in visited:
                    extract_url_content(
                        linked_url,
                        results,
                        visited,
                        recursive=recursive,
                        depth=depth + 1,
                        max_depth=max_depth,
                    )

    except Exception as e:
        print(f"  ✗ Error extracting {url}: {e}")


def main():
    """Main entry point."""
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

    try:
        print(f"Extracting from: {url}")
        results: list[dict] = []
        visited: set[str] = set()

        extract_url_content(url, results, visited, recursive=recursive)

        # Generate output filename
        filename = sanitize_filename(url) + ".json"
        output_path = Path(filename)

        counter = 1
        original_path = output_path
        while output_path.exists():
            output_path = original_path.with_name(
                f"{sanitize_filename(url)}_{counter}.json"
            )
            counter += 1

        # Write JSON file with all results
        output_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n✓ Saved {len(results)} page(s) to: {output_path}")

    except requests.RequestException as e:
        print(f"✗ Network error: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
