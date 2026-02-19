#!/usr/bin/env python3
"""Extract URL content and save to Markdown file."""

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


def extract_url_to_markdown(
    url: str,
    output_dir: Path = Path("."),
    visited: set[str] | None = None,
    recursive: bool = True,
    depth: int = 0,
    max_depth: int = 2,
) -> Path | None:
    """Fetch URL content and save as Markdown file."""
    if visited is None:
        visited = set()

    # Skip if already visited
    if url in visited:
        return None
    visited.add(url)

    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

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

    title = soup.find("title")
    title_text = title.get_text().strip() if title else "Untitled"

    # Extract linked URLs if recursive and within depth limit
    extracted_links: list[str] = []
    if recursive and depth < max_depth:
        linked_urls = extract_urls_from_markdown(markdown_content, url)
        if linked_urls:
            links_dir = output_dir / "linked"
            links_dir.mkdir(exist_ok=True)

            for linked_url in linked_urls:
                if linked_url not in visited:
                    try:
                        result = extract_url_to_markdown(
                            linked_url,
                            output_dir=links_dir,
                            visited=visited,
                            recursive=recursive,
                            depth=depth + 1,
                            max_depth=max_depth,
                        )
                        if result:
                            extracted_links.append(f"- {linked_url} → {result.name}")
                    except Exception as e:
                        extracted_links.append(f"- {linked_url} → Error: {e}")

    links_section = ""
    if extracted_links:
        links_section = "\n\n## Linked Pages Extracted\n\n" + "\n".join(extracted_links)

    depth_line = f"  \n**Depth:** {depth}" if depth > 0 else ""

    final_content = f"""# {title_text}

**Source:** {url}  
**Extracted:** {__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}{depth_line}

---

{markdown_content}{links_section}
"""

    filename = sanitize_filename(url) + ".md"
    output_path = output_dir / filename

    counter = 1
    original_path = output_path
    while output_path.exists():
        output_path = original_path.with_name(f"{sanitize_filename(url)}_{counter}.md")
        counter += 1

    output_path.write_text(final_content, encoding="utf-8")
    return output_path


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
        output_file = extract_url_to_markdown(url, recursive=recursive)
        if output_file:
            print(f"✓ Content saved to: {output_file}")
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
