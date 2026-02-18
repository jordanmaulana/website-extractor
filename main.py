#!/usr/bin/env python3
"""Extract URL content and save to Markdown file."""

import sys
import re
from urllib.parse import urlparse
from pathlib import Path

import certifi
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md


def sanitize_filename(url: str) -> str:
    """Create a safe filename from URL."""
    parsed = urlparse(url)
    name = f"{parsed.hostname or 'unknown'}{parsed.path}"
    name = re.sub(r'[^\w\-_.]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    if len(name) > 100:
        name = name[:100]
    return name or "extracted_content"


def extract_url_to_markdown(url: str, output_dir: Path = Path(".")) -> Path:
    """Fetch URL content and save as Markdown file."""
    if not url.startswith(('http://', 'https://')):
        raise ValueError("URL must start with http:// or https://")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0'
    }
    response = requests.get(url, headers=headers, timeout=30, verify=False)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, 'html.parser')

    for element in soup(['script', 'style', 'nav', 'footer', 'header']):
        element.decompose()

    main_content = soup.find('main') or soup.find('article') or soup.find('body')
    if not main_content:
        main_content = soup

    html_content = str(main_content)
    markdown_content = md(html_content, heading_style='ATX')

    lines = markdown_content.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped or cleaned_lines:
            cleaned_lines.append(line)
    markdown_content = '\n'.join(cleaned_lines).strip()

    title = soup.find('title')
    title_text = title.get_text().strip() if title else "Untitled"
    
    final_content = f"""# {title_text}

**Source:** {url}  
**Extracted:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

{markdown_content}
"""

    filename = sanitize_filename(url) + ".md"
    output_path = output_dir / filename

    counter = 1
    original_path = output_path
    while output_path.exists():
        output_path = original_path.with_name(f"{sanitize_filename(url)}_{counter}.md")
        counter += 1

    output_path.write_text(final_content, encoding='utf-8')
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

    try:
        output_file = extract_url_to_markdown(url)
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
