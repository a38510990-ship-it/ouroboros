"""
vision.py — VLM-based book recognition via OpenRouter API

Sends a bookshelf image to a vision-capable LLM and extracts a list
of recognized books (title + author pairs).

Supported models (set VLM_MODEL env var):
    google/gemini-2.0-flash-001       — fast, cheap, good quality (default)
    anthropic/claude-3.5-sonnet       — best quality, higher cost
    openai/gpt-4o                     — excellent alternative

The LLM is prompted to return a JSON array. The function parses the response
robustly, handling markdown code blocks and minor formatting issues.
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

# Prompt instructing the VLM what to extract
SYSTEM_PROMPT = """\
You are a book recognition assistant.
Your task: analyze a photo of a bookshelf and extract a list of all visible books.

For each book, return:
- title: the book title (as shown on the spine or cover)
- author: the author name (if visible or inferable)

Rules:
- If the author is not visible, use an empty string for the author field.
- Include all visible books, even partially visible ones where you can read the title.
- Do not include books you cannot identify at all.
- Return ONLY a JSON array. No markdown, no explanation.

Example output:
[
  {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
  {"title": "1984", "author": "George Orwell"},
  {"title": "Dune", "author": ""}
]
"""


async def recognize_books(image_bytes: bytes) -> list[dict]:
    """
    Recognize books in a bookshelf image using a vision LLM.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WEBP, or GIF)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Returns an empty list if no books are recognized or on error.

    Raises:
        RuntimeError: If the API key is missing or the API call fails fatally.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. "
            "Add it to bookshelf/.env"
        )

    # Encode image as base64 data URL
    mime_type = _detect_mime_type(image_bytes)
    b64_image = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64_image}"

    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1,  # Low temperature for consistent structured output
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros",
        "X-Title": "Bookshelf Catalog Bot",
    }

    logger.info(f"Sending image to VLM: {VLM_MODEL} ({len(image_bytes)} bytes)")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(
                OPENROUTER_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            raise RuntimeError(
                f"VLM request timed out after {REQUEST_TIMEOUT}s. "
                "Try again or use a faster model."
            )
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            raise RuntimeError(f"VLM API error {status}: {body}")

    data = response.json()
    raw_text = _extract_text(data)

    if not raw_text:
        logger.warning("VLM returned empty response")
        return []

    logger.debug(f"VLM raw response: {raw_text[:500]}")

    books = _parse_books_json(raw_text)
    logger.info(f"Recognized {len(books)} books")

    return books


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _extract_text(data: dict) -> str:
    """Extract text content from an OpenRouter API response."""
    try:
        choices = data.get("choices", [])
        if not choices:
            logger.warning("No choices in VLM response")
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return str(content).strip()
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"Could not extract text from VLM response: {e}")
        return ""


def _parse_books_json(text: str) -> list[dict]:
    """
    Parse the VLM response as a JSON array of books.

    Handles:
    - Clean JSON arrays
    - JSON wrapped in markdown code blocks (```json ... ```)
    - Minor formatting issues

    Returns an empty list if parsing fails.
    """
    # Strip markdown code blocks if present
    text = _strip_code_block(text)

    # Try parsing directly
    parsed = _try_parse_json(text)
    if parsed is not None:
        return _validate_books(parsed)

    # Try extracting the first JSON array found in the text
    array_match = re.search(r"\[.*?\]", text, re.DOTALL)
    if array_match:
        parsed = _try_parse_json(array_match.group(0))
        if parsed is not None:
            return _validate_books(parsed)

    logger.warning(f"Could not parse VLM response as JSON: {text[:300]}")
    return []


def _strip_code_block(text: str) -> str:
    """Remove markdown code block markers (```json ... ```)."""
    # Match ```json...``` or ```...```
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _try_parse_json(text: str) -> list | None:
    """Try to parse text as JSON. Returns None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        logger.warning(f"VLM returned JSON but not a list: {type(result)}")
        return None
    except json.JSONDecodeError:
        return None


def _validate_books(raw_list: list) -> list[dict]:
    """
    Validate and normalize the list of book dicts.

    - Each item must be a dict with at least a 'title' key.
    - 'author' defaults to empty string if missing.
    - Filters out empty titles.
    """
    books = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        author = str(item.get("author", "")).strip()
        books.append({"title": title, "author": author})
    return books


# ── MIME type detection ────────────────────────────────────────────────────────

def _detect_mime_type(image_bytes: bytes) -> str:
    """
    Detect image MIME type from file signature (magic bytes).

    Defaults to 'image/jpeg' if unrecognized.
    """
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"GIF":
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # JPEG: starts with FF D8
    if image_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    # Default
    return "image/jpeg"
