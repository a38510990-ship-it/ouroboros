"""
Vision module: recognize books on a bookshelf photo using a VLM via OpenRouter.

Sends the image as base64 to a vision-capable model and parses the response
into a list of {title, author} dicts.
"""
import base64
import json
import logging
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Default model — good vision + fast
DEFAULT_MODEL = "google/gemini-2.0-flash-001"

PROMPT = """You are a book cataloging assistant. Look at this photo of a bookshelf.

List ALL books you can see, reading the text on the spines.
Include both vertical and horizontal books.

Respond ONLY with a JSON array, no markdown, no explanation:
[
  {"title": "Book Title", "author": "Author Name"},
  {"title": "Another Book", "author": "Unknown"}
]

Rules:
- If you can't read the author, use "Unknown"
- Include partial titles if spine is partially visible
- Do not include books you're not sure about
- Do not add any text outside the JSON array
"""


def recognize_books(
    image_path: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """
    Send a bookshelf image to VLM and return list of recognized books.

    Args:
        image_path: Path to the image file (jpg/png/webp)
        api_key: OpenRouter API key
        model: Vision model to use (must support image input)

    Returns:
        List of dicts: [{title: str, author: str}, ...]
    """
    # Read and encode image
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_bytes = path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Detect media type
    suffix = path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(suffix, "image/jpeg")

    logger.info(f"Sending image to {model} ({len(image_bytes) // 1024} KB, {media_type})")

    # Build OpenRouter request
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros/bookshelf-catalog",
        "X-Title": "Bookshelf Catalog Bot",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT,
                    },
                ],
            }
        ],
        "temperature": 0.1,  # low temp for structured output
        "max_tokens": 2000,
    }

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter API error {response.status_code}: {response.text[:500]}"
        )

    result = response.json()
    raw_text = result["choices"][0]["message"]["content"].strip()
    logger.debug(f"VLM raw response: {raw_text[:500]}")

    return _parse_books_json(raw_text)


def _parse_books_json(text: str) -> list[dict]:
    """
    Parse VLM response into list of book dicts.
    Handles cases where model wraps JSON in markdown code blocks.
    """
    # Strip markdown code blocks if present
    text = text.strip()
    if text.startswith("```"):
        # Remove ```json or ``` at start and ``` at end
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    try:
        books = json.loads(text)
        if not isinstance(books, list):
            logger.warning(f"VLM returned non-list JSON: {type(books)}")
            return []

        # Validate and clean each entry
        cleaned = []
        for item in books:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            author = str(item.get("author", "")).strip()
            if title:
                cleaned.append({"title": title, "author": author or "Unknown"})

        logger.info(f"Parsed {len(cleaned)} books from VLM response")
        return cleaned

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse VLM response as JSON: {e}")
        logger.error(f"Raw text: {text[:300]}")
        # Try to salvage partial data
        return _fallback_parse(text)


def _fallback_parse(text: str) -> list[dict]:
    """
    Fallback: try to extract book titles from plain text if JSON fails.
    Returns whatever we can salvage.
    """
    books = []
    # Look for quoted strings or lines that look like book titles
    lines = text.split("\n")
    for line in lines:
        line = line.strip().strip("•-*").strip()
        if len(line) > 3 and not line.startswith(("{", "[", "}")):
            books.append({"title": line, "author": "Unknown"})

    if books:
        logger.warning(f"Fallback parse found {len(books)} potential titles")
    return books
