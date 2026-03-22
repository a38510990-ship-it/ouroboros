"""
vision.py — VLM-based book recognition via OpenRouter API

Sends a base64-encoded photo to a vision model and parses the response
into a list of {title, author} dicts.
"""

import asyncio
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
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default model — change in .env if needed
DEFAULT_VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")

SYSTEM_PROMPT = """You are a book recognition expert. 
The user will send you a photo of a bookshelf.
Your task is to identify all visible books by reading their spines.

Respond ONLY with a JSON array. Each element is an object with:
- "title": book title (string)
- "author": author name (string, or "" if not visible)

Example response:
[
  {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
  {"title": "1984", "author": "George Orwell"},
  {"title": "Sapiens", "author": "Yuval Noah Harari"}
]

Rules:
- Include ALL visible books, even if you can only read part of the title
- If author is not visible on the spine, use "" (empty string)
- Do not include books you cannot read at all
- Return ONLY the JSON array, no other text
- If no books are visible, return []
"""


async def recognize_books(image_data: bytes, model: str | None = None) -> list[dict]:
    """
    Send image to VLM and parse book list.

    Args:
        image_data: raw image bytes (JPEG, PNG, etc.)
        model: OpenRouter model ID (defaults to DEFAULT_VLM_MODEL)

    Returns:
        list of dicts with 'title' and 'author' keys
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError("OPENROUTER_API_KEY not set in .env")

    model = model or DEFAULT_VLM_MODEL

    # Encode image as base64
    b64_image = base64.b64encode(image_data).decode("utf-8")

    # Detect MIME type (basic heuristic)
    mime_type = _detect_mime_type(image_data)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_image}",
                        },
                    },
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1,  # Low temperature for consistent parsing
    }

    logger.info(f"Sending image to VLM: model={model}, size={len(image_data)} bytes")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/ouroboros",
                "X-Title": "Bookshelf Catalog Bot",
            },
            json=payload,
        )

        if response.status_code != 200:
            logger.error(f"OpenRouter error {response.status_code}: {response.text[:500]}")
            raise RuntimeError(
                f"VLM API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()

    # Extract text from response
    content = data["choices"][0]["message"]["content"]
    logger.info(f"VLM response ({len(content)} chars): {content[:300]}")

    # Parse JSON from response
    books = _parse_books_json(content)
    logger.info(f"Parsed {len(books)} books from VLM response")

    return books


def _detect_mime_type(image_data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_data[:2] == b"\xff\xd8":
        return "image/jpeg"
    elif image_data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return "image/webp"
    elif image_data[:3] == b"GIF":
        return "image/gif"
    else:
        return "image/jpeg"  # fallback


def _parse_books_json(content: str) -> list[dict]:
    """
    Parse JSON array from VLM response.
    Handles cases where model wraps JSON in markdown code blocks.
    """
    content = content.strip()

    # Try direct parse first
    try:
        result = json.loads(content)
        if isinstance(result, list):
            return _normalize_books(result)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code block
    # Pattern: ```json\n[...]\n``` or ```\n[...]\n```
    code_block_pattern = r"```(?:json)?\s*(\[.*?\])\s*```"
    match = re.search(code_block_pattern, content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list):
                return _normalize_books(result)
        except json.JSONDecodeError:
            pass

    # Try finding any JSON array in the text
    array_pattern = r"\[.*?\]"
    match = re.search(array_pattern, content, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                return _normalize_books(result)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse JSON from VLM response: {content[:200]}")
    return []


def _normalize_books(raw_list: list) -> list[dict]:
    """
    Normalize raw VLM output to standard format.
    Filters out entries with no title.
    """
    books = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("Title") or "").strip()
        author = (item.get("author") or item.get("Author") or "").strip()
        if title:
            books.append({"title": title, "author": author})
    return books
