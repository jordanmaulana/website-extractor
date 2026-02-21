"""Celery tasks for website extraction."""

import json
import re
import time
from urllib.parse import urlparse

import certifi
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from markdownify import markdownify as md
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


def sanitize_filename(url: str) -> str:
    """Create a safe filename from URL."""
    parsed = urlparse(url)
    name = f"{parsed.hostname or 'unknown'}{parsed.path}"
    name = re.sub(r"[^\w\-_.]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if len(name) > 100:
        name = name[:100]
    return name or "extracted_content"


def extract_images_from_markdown(markdown_content: str) -> tuple[str, list[str]]:
    """Extract image URLs from markdown and return cleaned content + images list."""
    images: list[str] = []

    # Find markdown images ![alt](url)
    md_images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", markdown_content)
    for _, url in md_images:
        images.append(url.strip())

    # Remove markdown images from content
    content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", markdown_content)

    # Find bare image URLs
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
    bare_urls = re.findall(r"https?://[^\s\)\]\>\"\']+", content)

    for url in bare_urls:
        url_lower = url.lower().split("?")[0]
        if url_lower.endswith(image_extensions):
            images.append(url)

    # Remove bare image URLs from content (replace with empty string)
    for url in images:
        content = content.replace(url, "")

    # Clean up multiple newlines and spaces
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = re.sub(r" {2,}", " ", content)
    content = content.strip()

    return content, images


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
            # Relative URL starting with / - join with base
            parsed_base = urlparse(base_url)
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
            if not is_image_url(full_url):
                urls.add(full_url)
        elif not url.startswith(("#", "javascript:", "mailto:", "tel:")) and "." in url:
            # Relative URL without leading slash (e.g., "en-US/text/...")
            # Join with base URL path
            parsed_base = urlparse(base_url)
            base_path = parsed_base.path.rsplit("/", 1)[0]  # Remove filename if any
            if not base_path.endswith("/"):
                base_path += "/"
            full_url = f"{parsed_base.scheme}://{parsed_base.netloc}{base_path}{url}"
            # Normalize the URL (remove ../, ./)
            full_url = full_url.replace("/../", "/").replace("/./", "/")
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


def fetch_with_selenium(url: str) -> str:
    """Fetch page content using headless Chrome browser."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(url)

        # Wait for page to load with longer timeout
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(("tag name", "body"))
        )

        # Additional wait for JavaScript to render
        time.sleep(5)

        html = driver.page_source
        return html
    finally:
        driver.quit()


def fetch_page_content(url: str, use_selenium: bool = False) -> str:
    """Fetch page content using requests or selenium."""
    if use_selenium:
        return fetch_with_selenium(url)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0"
    }
    response = requests.get(url, headers=headers, timeout=30, verify=certifi.where())
    response.raise_for_status()
    return response.text


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def extract_single_url(
    self,
    url: str,
    include_images: bool = False,
    use_selenium: bool = False,
) -> dict:
    """Extract content from a single URL."""
    try:
        html = fetch_page_content(url, use_selenium=use_selenium)
        soup = BeautifulSoup(html, "html.parser")

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

        # Extract images and clean content
        if include_images:
            cleaned_content, images = extract_images_from_markdown(markdown_content)
        else:
            cleaned_content = markdown_content
            images = []

        return {
            "url": url,
            "content": cleaned_content,
            "images": images,
            "linked_urls": list(extract_urls_from_markdown(markdown_content, url)),
        }
    except Exception as exc:
        # Retry on failure
        raise self.retry(exc=exc)


@shared_task
def extract_website_recursive(
    start_url: str,
    max_depth: int = 5,
    include_images: bool = False,
    use_selenium: bool = False,
) -> list[dict]:
    """Recursively extract website content using Celery tasks."""
    results: list[dict] = []
    visited: set[str] = set()
    urls_to_process: list[tuple[str, int]] = [(start_url, 0)]

    while urls_to_process:
        current_batch = []
        next_batch: list[tuple[str, int]] = []

        # Process URLs at current depth level
        for url, depth in urls_to_process:
            if url in visited or depth > max_depth:
                continue
            visited.add(url)
            current_batch.append((url, depth))

        if not current_batch:
            break

        # Launch tasks for current batch
        task_signatures = [
            extract_single_url.s(url, include_images, use_selenium)
            for url, _ in current_batch
        ]

        # Execute tasks in parallel and get results
        from celery import group

        job = group(task_signatures)
        batch_results = job.apply_async().get()

        # Process results and collect linked URLs for next depth
        for (url, depth), result in zip(current_batch, batch_results):
            if result:
                results.append(
                    {
                        "url": result["url"],
                        "content": result["content"],
                        "images": result["images"],
                    }
                )

                # Add linked URLs for next depth level
                if depth < max_depth:
                    for linked_url in result["linked_urls"]:
                        if linked_url not in visited:
                            next_batch.append((linked_url, depth + 1))

        urls_to_process = next_batch

    return results


@shared_task
def save_results_to_json(results: list[dict], url: str) -> str:
    """Save extraction results to JSON file."""
    from pathlib import Path

    filename = sanitize_filename(url) + ".json"
    output_path = Path(filename)

    counter = 1
    original_path = output_path
    while output_path.exists():
        output_path = original_path.with_name(
            f"{sanitize_filename(url)}_{counter}.json"
        )
        counter += 1

    output_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return str(output_path)
