"""
vision.py — Book recognition via OpenRouter VLM

Sends a bookshelf photo to a vision model and extracts book titles and authors.

Supported models (set VLM_MODEL in .env):
    google/gemini-2.0-flash-001        — fast, cheap, good default
    anthropic/claude-3.5-sonnet        — best accuracy
    openai/gpt-4o                      — excellent alternative

The model is asked to return JSON: [{"title": "...", "author": "..."}]
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 60.0

SYSTEM_PROMPT = (
    "You are a book recognition assistant. "
    "When given a photo of a bookshelf, identify all visible books and return their details as JSON. "
    "Return ONLY a valid JSON array, no other text. "
    'Format: [{"title": "Book Title", "author": "Author Name"}, ...]'
)

USER_PROMPT = (
    "Look at this bookshelf photo. "
    "Identify all books you can see (reading the spines). "
    "Return a JSON array with title and author for each book. "
    "If you cannot read an author name, use an empty string. "
    "If you cannot clearly read a title, skip that book. "
    "Return ONLY the JSON array, nothing else."
)


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a bookshelf photo.

    Args:
        image_bytes: Raw image data (JPEG, PNG, etc.)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Empty list if no books found or recognition failed.

    Raises:
        RuntimeError: If the API call fails (no key, network error, etc.)
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to bookshelf/.env"
        )

    # Encode image as base64
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = _detect_mime_type(image_bytes)

    # Build request
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT,
                    },
                ],
            },
        ],
        "max_tokens": 2000,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros/bookshelf",
        "X-Title": "Bookshelf Catalog Bot",
    }

    logger.info(f"Sending image to VLM ({VLM_MODEL}), size: {len(image_bytes)} bytes")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                OPENROUTER_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"API request failed: {e.response.status_code} {e.response.text}")
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error: {e}")

    data = response.json()

    # Extract text content
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected API response: {data}")
        raise RuntimeError(f"Unexpected API response structure: {e}")

    logger.info(f"VLM response received ({len(content)} chars)")
    logger.debug(f"VLM response: {content[:500]}")

    # Parse JSON from response
    books = _parse_books(content)
    logger.info(f"Recognized {len(books)} book(s)")

    return books


def _parse_books(content: str) -> list[dict]:
    """
    Parse JSON books list from VLM response.

    Handles:
    - Pure JSON: [{"title": "...", "author": "..."}]
    - JSON wrapped in markdown code block: ```json\n[...]\n```
    - JSON embedded in text
    """
    content = content.strip()

    # Try direct JSON parse
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return _validate_books(data)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    code_block = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    # Try finding first JSON array in the text
    array_match = re.search(r"\[.*\]", content, re.DOTALL)
    if array_match:
        try:
            data = json.loads(array_match.group(0))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse books from response: {content[:200]}")
    return []


def _validate_books(data: list) -> list[dict]:
    """Filter and normalize book entries from parsed JSON."""
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        author = str(item.get("author", "")).strip()
        if title:  # Title is required
            result.append({"title": title, "author": author})
    return result


def _detect_mime_type(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    elif image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    else:
        return "image/jpeg"  # Default fallback
