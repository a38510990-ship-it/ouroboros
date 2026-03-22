"""
vision.py — Book recognition via OpenRouter VLM

Sends a shelf photo to a vision-capable LLM and parses the response
into a list of {title, author} dicts.

Supported models (via OPENROUTER_API_KEY):
    google/gemini-2.0-flash-001    — fast, cheap, good (default)
    anthropic/claude-3.5-sonnet   — best quality
    openai/gpt-4o                 — excellent alternative
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

VISION_PROMPT = """You are a book catalog assistant. 
Look at this photo of a bookshelf and identify every book visible.

For each book, extract:
- title: The book title as written on the spine
- author: The author's name (if visible)

Return ONLY a JSON array. Example:
[
  {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
  {"title": "1984", "author": "George Orwell"},
  {"title": "Some Book With No Author", "author": ""}
]

Rules:
- Include every book you can see, even if only partially visible
- If author is not visible, use empty string
- If you can't read the title clearly, make your best guess
- Return ONLY the JSON array, nothing else
"""


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a shelf photo.

    Args:
        image_bytes: Raw image bytes (JPEG or PNG)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Returns empty list if no books found.

    Raises:
        RuntimeError: If the API call fails or returns an error.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to bookshelf/.env"
        )

    # Detect image MIME type from magic bytes
    mime_type = _detect_mime(image_bytes)
    logger.info(f"Sending {len(image_bytes)} bytes ({mime_type}) to {VLM_MODEL}")

    # Encode image as base64
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    data_url = f"data:{mime_type};base64,{image_b64}"

    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                    {
                        "type": "text",
                        "text": VISION_PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros",
        "X-Title": "Bookshelf Catalog Bot",
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise RuntimeError(f"OpenRouter API error {e.response.status_code}: {body}")
        except httpx.TimeoutException:
            raise RuntimeError("OpenRouter API timed out. Please try again.")
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}")

    data = response.json()

    # Extract text from response
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected API response format: {data}")

    if not content:
        logger.warning("VLM returned empty content")
        return []

    logger.debug(f"VLM response: {content[:300]}...")

    books = _parse_books(content)
    logger.info(f"Recognized {len(books)} book(s)")
    return books


def _detect_mime(image_bytes: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # Default to JPEG (most common for Telegram photos)
    return "image/jpeg"


def _parse_books(content: str) -> list[dict]:
    """
    Parse VLM response into a list of book dicts.

    Tries multiple strategies:
    1. Direct JSON parse
    2. Extract JSON array from text (in case of extra prose)
    3. Return empty list on total failure
    """
    content = content.strip()

    # Strategy 1: direct JSON parse
    try:
        result = json.loads(content)
        if isinstance(result, list):
            return _validate_books(result)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract JSON array from text
    match = re.search(r"\[.*?\]", content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return _validate_books(result)
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract JSON with code fence
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list):
                return _validate_books(result)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse VLM response as JSON: {content[:200]}")
    return []


def _validate_books(raw: list) -> list[dict]:
    """
    Validate and normalize book dicts from VLM.

    Filters out items without a title.
    Normalizes 'title' and 'author' fields.
    """
    books = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        author = str(item.get("author", "")).strip()

        if not title:
            continue

        books.append({"title": title, "author": author})

    return books
