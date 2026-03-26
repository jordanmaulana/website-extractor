# Website Extractor

A Python tool that extracts content from websites and converts it to structured JSON format. It can crawl multiple pages recursively and handle JavaScript-rendered content.

## Features

- **Content Extraction**: Extracts text content from web pages and converts HTML to clean Markdown
- **Recursive Crawling**: Automatically follows links within the same domain (configurable depth)
- **JavaScript Support**: Optional Selenium mode for pages that require JavaScript rendering
- **Image Extraction**: Optionally extract image URLs from pages
- **Smart Filtering**: Ignores navigation, footers, headers, and script content
- **Output Format**: Saves extracted content as structured JSON with URLs, content, and image lists

## Requirements

- Python 3.10 or higher
- Chrome/Chromium browser (for Selenium mode)
- uv (recommended for dependency management)

## Installation

1. Clone the repository:

```bash
git clone <repository-url>
cd website-extractor
```

2. Install dependencies using uv:

```bash
uv sync
```

## Usage

### Basic Usage

Extract content from a single URL:

```bash
uv run python main.py https://example.com
```

Or run directly:

```bash
python main.py https://example.com
```

### Command Line Options

- `--no-recursive`: Disable recursive crawling (default: enabled)
- `--include-images`: Extract image URLs from pages
- `--selenium`: Use headless Chrome browser for JavaScript rendering

### Examples

1. **Basic extraction with recursive crawling:**

```bash
uv run python main.py https://uhudtour.com
```

2. **Single page without recursion:**

```bash
uv run python main.py https://uhudtour.com --no-recursive
```

3. **Extract with images:**

```bash
uv run python main.py https://uhudtour.com --include-images
```

4. **JavaScript-heavy site:**

```bash
uv run python main.py https://uhudtour.com --selenium
```

5. **Full extraction with all features:**

```bash
uv run python main.py https://uhudtour.com --include-images --selenium
```

## Output

The tool creates a JSON file with the extracted content. The filename is generated from the URL (e.g., `example_com.json`). If the file exists, a counter is appended.

### JSON Structure

```json
[
  {
    "url": "https://example.com/page1",
    "content": "Cleaned markdown content...",
    "images": [
      "https://example.com/image1.jpg",
      "https://example.com/image2.png"
    ]
  },
  {
    "url": "https://example.com/page2",
    "content": "More markdown content...",
    "images": []
  }
]
```

## Development

### Code Quality

Run linting and formatting:

```bash
make lint
```

### Testing

Test with a documentation site (uses Selenium):

```bash
make test
```

Test with image extraction:

```bash
make test-images
```

### Dependency Management

Upgrade dependencies:

```bash
make upgrade
```

## How It Works

1. **Content Fetching**: Uses `requests` by default, falls back to Selenium for JavaScript pages
2. **HTML Processing**: Removes unwanted elements (scripts, styles, nav, footer, header)
3. **Content Extraction**: Finds main content area (main/article/body tags)
4. **Markdown Conversion**: Converts cleaned HTML to Markdown using `markdownify`
5. **Link Discovery**: Extracts same-domain links for recursive crawling
6. **Image Processing**: Optionally finds and extracts image URLs
7. **Output**: Saves all extracted pages to a structured JSON file

## Configuration

- **Max Depth**: Recursive crawling limited to 5 levels by default
- **Timeout**: 30-second timeout for HTTP requests
- **User Agent**: Uses a realistic browser user agent string
- **Wait Time**: 5-second wait for JavaScript rendering in Selenium mode

## Dependencies

- `requests`: HTTP client for basic fetching
- `beautifulsoup4`: HTML parsing and cleaning
- `markdownify`: HTML to Markdown conversion
- `selenium`: Headless browser automation
- `webdriver-manager`: Automatic Chrome driver management
- `certifi`: SSL certificate verification

## License

This project is open source. Please refer to the license file for details.
