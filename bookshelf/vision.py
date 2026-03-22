"""
vision.py — VLM (Vision Language Model) integration via OpenRouter

Uses a vision-capable model to recognize books from a bookshelf photo.
Returns a list of {title, author} dicts for further enrichment.

Supported models (via OpenRouter):
    - google/gemini-2.0-flash-001      (default: fast, cheap)
    - anthropic/claude-3.5-sonnet      (excellent quality)
    - openai/gpt-4o                    (excellent quality)
    - google/gemini-2.5-pro-preview    (best quality)
"""

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 90.0

RECOGNITION_PROMPT = """You are analyzing a photo of a bookshelf. Your task is to identify all visible books.

For each book you can see (fully or partially), extract:
- title: the book's title (as accurately as possible)
- author: the author's name (if visible on the spine)

Return ONLY a JSON array. No explanations, no markdown, just JSON.

Format:
[
  {"title": "Book Title", "author": "Author Name"},
  {"title": "Another Book", "author": ""},
  ...
]

Rules:
- Include ALL visible books, even if you can only read part of the title
- If author is not visible on the spine, use empty string ""
- Correct obvious OCR errors (e.g., "Hairy Potter" → "Harry Potter")
- If you cannot identify any books at all, return an empty array: []
"""


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a bookshelf photo using a VLM.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WebP, etc.)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Returns empty list if no books recognized.

    Raises:
        EnvironmentError: If OPENROUTER_API_KEY is not set.
        RuntimeError: If the API request fails after retries.
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to bookshelf/.env or environment variables."
        )

    # Detect MIME type from image header bytes
    mime_type = _detect_mime_type(image_bytes)
    logger.info(f"Processing image: {len(image_bytes)} bytes, MIME: {mime_type}, model: {VLM_MODEL}")

    # Encode image to base64
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    image_url = f"data:{mime_type};base64,{image_b64}"

    # Build the request
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
        "max_tokens": 2048,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros-bot/bookshelf",
        "X-Title": "Bookshelf Catalog Bot",
    }

    # Make API request with retry
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:500]
            logger.error(f"OpenRouter API error {e.response.status_code}: {error_body}")
            raise RuntimeError(
                f"VLM API returned {e.response.status_code}: {error_body}"
            ) from e
        except httpx.RequestError as e:
            logger.error(f"Network error calling OpenRouter: {e}")
            raise RuntimeError(f"Network error: {e}") from e

    # Parse response
    data = response.json()
    raw_text = _extract_text(data)
    logger.debug(f"VLM response: {raw_text[:200]}")

    books = _parse_books(raw_text)
    logger.info(f"Recognized {len(books)} books")
    return books


def _detect_mime_type(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    elif image_bytes[:4] in (b"GIF8", b"GIF9"):
        return "image/gif"
    else:
        # Default to JPEG (most common for photos)
        return "image/jpeg"


def _extract_text(api_response: dict) -> str:
    """Extract text content from OpenRouter API response."""
    try:
        choices = api_response.get("choices", [])
        if not choices:
            logger.warning("No choices in VLM response")
            return "[]"
        message = choices[0].get("message", {})
        content = message.get("content", "[]")
        if not content:
            return "[]"
        return content
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"Error parsing VLM response structure: {e}")
        return "[]"


def _parse_books(text: str) -> list[dict]:
    """
    Parse VLM output into a list of book dicts.

    Handles:
    - Clean JSON arrays
    - JSON wrapped in markdown code blocks
    - Partial/malformed JSON (best effort)
    """
    if not text or not text.strip():
        return []

    # Try direct JSON parse first
    try:
        data = json.loads(text.strip())
        if isinstance(data, list):
            return _validate_books(data)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code blocks
    # e.g., ```json\n[...]\n```
    code_block_pattern = r"```(?:json)?\s*(\[.*?\])\s*```"
    match = re.search(code_block_pattern, text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    # Try to find any JSON array in the text
    array_pattern = r"\[.*?\]"
    match = re.search(array_pattern, text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse VLM output as JSON: {text[:200]}")
    return []


def _validate_books(raw_list: list) -> list[dict]:
    """
    Validate and normalize a list of book dicts.

    Filters out entries without a title.
    Normalizes keys to lowercase 'title' and 'author'.
    """
    books = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue

        # Normalize keys (handle capitalized variants)
        title = str(
            item.get("title") or item.get("Title") or ""
        ).strip()
        author = str(
            item.get("author") or item.get("Author") or ""
        ).strip()

        if title:
            books.append({"title": title, "author": author})

    return books
