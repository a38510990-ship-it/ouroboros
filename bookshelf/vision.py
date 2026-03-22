"""
vision.py — Book recognition via Vision LLM (OpenRouter)

Takes a photo of a bookshelf and returns a list of recognized books.
Each book is a dict with 'title' and 'author' keys.

Uses OpenRouter API with a vision-capable model.

Supported models (set via VLM_MODEL env variable):
    google/gemini-2.0-flash-001   — fast, cheap, good quality (default)
    anthropic/claude-3.5-sonnet   — best quality
    openai/gpt-4o                 — excellent alternative

The model returns structured JSON which is parsed and validated.
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load env (in case this module is imported before bot.py sets it up)
HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DEFAULT_VLM_MODEL = "google/gemini-2.0-flash-001"

# Prompt for the VLM
RECOGNITION_PROMPT = """Look at this bookshelf photo carefully.

Your task: identify ALL books visible in the image where you can read the spine text.

For each book you can read:
- Extract the TITLE (exactly as written on the spine)
- Extract the AUTHOR (if visible on the spine)

Return a JSON array. Example:
[
  {"title": "The Name of the Wind", "author": "Patrick Rothfuss"},
  {"title": "Sapiens", "author": "Yuval Noah Harari"},
  {"title": "1984", "author": "George Orwell"}
]

Rules:
- Include ONLY books where you can clearly read the title
- If the author is not visible on the spine, leave "author" as empty string ""
- Return ONLY the JSON array, no other text
- If no books are readable, return an empty array: []
"""


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a bookshelf photo using a vision LLM.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, etc.)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Returns empty list if no books recognized.

    Raises:
        RuntimeError: If OPENROUTER_API_KEY is not set.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Please add it to bookshelf/.env"
        )

    model = os.getenv("VLM_MODEL", DEFAULT_VLM_MODEL)
    logger.info(f"Recognizing books with model: {model}")

    # Encode image as base64
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    mime_type = _detect_mime_type(image_bytes)
    logger.debug(f"Image: {len(image_bytes)} bytes, type: {mime_type}")

    # Build request
    payload = {
        "model": model,
        "max_tokens": 2000,
        "messages": [
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
                        "text": RECOGNITION_PROMPT,
                    },
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros",
        "X-Title": "Bookshelf Catalog Bot",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                OPENROUTER_API_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"OpenRouter API error: {e.response.status_code} — {e.response.text}")
            raise RuntimeError(f"VLM API error: {e.response.status_code}") from e
        except httpx.TimeoutException:
            raise RuntimeError("VLM request timed out (>60s)")

    data = response.json()

    # Extract text content from response
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        logger.error(f"Unexpected API response structure: {data}")
        raise RuntimeError(f"Unexpected VLM response format") from e

    logger.debug(f"VLM response: {content[:500]}...")

    # Parse the JSON book list
    books = _parse_book_list(content)
    logger.info(f"Recognized {len(books)} books")

    return books


def _parse_book_list(content: str) -> list[dict]:
    """
    Parse the VLM response into a list of book dicts.

    Handles:
    - Clean JSON arrays
    - JSON wrapped in ```json ... ``` code blocks
    - JSON embedded in explanatory text
    """
    if not content:
        return []

    # Try direct parse first
    stripped = content.strip()
    if stripped.startswith("["):
        try:
            books = json.loads(stripped)
            return _validate_books(books)
        except json.JSONDecodeError:
            pass

    # Try to extract JSON from code blocks
    code_block_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if code_block_match:
        try:
            books = json.loads(code_block_match.group(1))
            return _validate_books(books)
        except json.JSONDecodeError:
            pass

    # Try to find any JSON array in the text
    array_match = re.search(r"\[.*?\]", content, re.DOTALL)
    if array_match:
        try:
            books = json.loads(array_match.group(0))
            return _validate_books(books)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse VLM response as JSON: {content[:200]}")
    return []


def _validate_books(raw: list) -> list[dict]:
    """
    Validate and clean the parsed book list.

    Ensures each item has 'title' and 'author' keys.
    Filters out items without a title.
    """
    if not isinstance(raw, list):
        logger.warning(f"Expected list, got {type(raw)}")
        return []

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


def _detect_mime_type(image_bytes: bytes) -> str:
    """
    Detect MIME type from image magic bytes.

    Supports: JPEG, PNG, GIF, WebP, BMP.
    Defaults to image/jpeg for unknown types.
    """
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    elif image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    elif image_bytes[:2] == b"BM":
        return "image/bmp"
    else:
        return "image/jpeg"  # Safe default
