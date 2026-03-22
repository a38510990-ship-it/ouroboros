"""
vision.py — Book recognition via VLM (Vision Language Model)

Uses OpenRouter API to analyze bookshelf photos and extract book information.
Returns a list of {title, author} dicts for further enrichment.

Supported models (set via VLM_MODEL env var):
    - google/gemini-2.0-flash-001 (default — fast, cheap, good quality)
    - anthropic/claude-3.5-sonnet (best quality)
    - openai/gpt-4o (excellent quality)

The prompt is designed to extract book spine text reliably,
including books with obscured, partial, or stylized spines.
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Prompt for book recognition
RECOGNITION_PROMPT = """You are analyzing a photo of a bookshelf.

Your task: identify ALL books visible in the photo.

For each book you can see (even partially), extract:
1. Title (as printed on the spine/cover)
2. Author (if visible on the spine/cover)

Rules:
- Include ALL books, even if you can only read part of the title
- If the author is not visible, leave it empty
- Do NOT make up information — only what you can actually see
- Books may be horizontal or vertical
- Include books in any language

Return a JSON array. Example:
[
  {"title": "The Pragmatic Programmer", "author": "David Thomas"},
  {"title": "Clean Code", "author": "Robert C. Martin"},
  {"title": "Дюна", "author": "Фрэнк Херберт"}
]

If no books are visible, return an empty array: []
Return ONLY the JSON array, no other text."""


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a bookshelf photo using VLM.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WebP, etc.)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Returns empty list if no books found or on error.

    Raises:
        RuntimeError: If OpenRouter API key is not set.
        httpx.HTTPError: If the API request fails.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to bookshelf/.env"
        )

    # Detect image MIME type
    mime_type = _detect_mime_type(image_bytes)
    logger.info(f"Processing image: {len(image_bytes)} bytes, type: {mime_type}")

    # Encode to base64
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    image_url = f"data:{mime_type};base64,{image_b64}"

    # Build request
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                    {
                        "type": "text",
                        "text": RECOGNITION_PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.1,  # Low temperature for factual extraction
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros-project/bookshelf",
        "X-Title": "Bookshelf Catalog Bot",
    }

    logger.info(f"Sending image to VLM: {VLM_MODEL}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    # Extract text response
    content = data["choices"][0]["message"]["content"]
    logger.debug(f"VLM raw response: {content[:500]}")

    # Parse JSON from response
    books = _parse_books_json(content)
    logger.info(f"Recognized {len(books)} books")

    return books


def _detect_mime_type(image_bytes: bytes) -> str:
    """
    Detect image MIME type from file signature (magic bytes).

    Defaults to image/jpeg if unknown.
    """
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    elif image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif image_bytes[:4] in (b"RIFF", b"WEBP"):
        return "image/webp"
    elif image_bytes[:4] == b"GIF8":
        return "image/gif"
    elif image_bytes[:2] in (b"BM",):
        return "image/bmp"
    else:
        return "image/jpeg"  # Default assumption


def _parse_books_json(content: str) -> list[dict]:
    """
    Parse the VLM response to extract a list of books.

    Handles cases where:
    - Response is clean JSON
    - Response has markdown code blocks (```json ... ```)
    - Response has extra text before/after JSON

    Returns:
        List of {title, author} dicts. Empty list if parsing fails.
    """
    # Try direct JSON parse first
    try:
        data = json.loads(content.strip())
        if isinstance(data, list):
            return _normalize_book_list(data)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON array from markdown code block
    code_block_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if code_block_match:
        try:
            data = json.loads(code_block_match.group(1))
            if isinstance(data, list):
                return _normalize_book_list(data)
        except json.JSONDecodeError:
            pass

    # Try to find any JSON array in the text
    array_match = re.search(r"\[.*?\]", content, re.DOTALL)
    if array_match:
        try:
            data = json.loads(array_match.group(0))
            if isinstance(data, list):
                return _normalize_book_list(data)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse VLM response as JSON: {content[:300]}")
    return []


def _normalize_book_list(raw_list: list) -> list[dict]:
    """
    Normalize raw JSON list to clean {title, author} dicts.

    Handles variations in key names:
    - "title", "Title", "book_title", "name"
    - "author", "Author", "authors", "writer"
    """
    books = []

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        # Extract title — try multiple possible key names
        title = (
            item.get("title")
            or item.get("Title")
            or item.get("book_title")
            or item.get("name")
            or ""
        )

        # Extract author — try multiple possible key names
        author_raw = (
            item.get("author")
            or item.get("Author")
            or item.get("authors")
            or item.get("writer")
            or ""
        )

        # Handle list of authors
        if isinstance(author_raw, list):
            author = ", ".join(str(a) for a in author_raw if a)
        else:
            author = str(author_raw) if author_raw else ""

        title = str(title).strip()
        author = author.strip()

        # Skip empty entries
        if not title:
            continue

        books.append({"title": title, "author": author})

    return books
