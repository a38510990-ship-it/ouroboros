"""
vision.py — VLM-based book recognition via OpenRouter

Takes a photo (bytes) of a bookshelf and returns a list of books
recognized from the spine titles/authors.

Supported models (set VLM_MODEL in .env):
    google/gemini-2.0-flash-001         — fast and cheap (default)
    anthropic/claude-3.5-sonnet         — excellent quality
    openai/gpt-4o                       — excellent quality
    google/gemini-2.5-pro-preview       — best quality
"""

import base64
import json
import logging
import os
import re

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemini-2.0-flash-001")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 60.0

SYSTEM_PROMPT = """You are a book recognition expert. 
You will be given a photo of a bookshelf and must identify all visible books.
Return ONLY a valid JSON array of books, no other text.
Each book object must have: "title" and "author" fields.
If author is not visible or unclear, use an empty string "".
Include ALL books you can see, even partially visible ones.
Example: [{"title": "Dune", "author": "Frank Herbert"}, {"title": "1984", "author": "George Orwell"}]"""

USER_PROMPT = """Look at this bookshelf photo. 
Identify ALL books visible on the shelves by reading their spine labels.
Return a JSON array with title and author for each book.
Only return the JSON array, nothing else."""


async def recognize_books(image_data: bytes) -> list[dict]:
    """
    Recognize books in a shelf photo using a VLM via OpenRouter.

    Args:
        image_data: Raw image bytes (JPEG, PNG, WebP)

    Returns:
        List of dicts with 'title' and 'author' keys.
        Empty list if no books found or recognition failed.

    Raises:
        EnvironmentError: if OPENROUTER_API_KEY is not set
        RuntimeError: if API call fails
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError(
            "OPENROUTER_API_KEY not set! "
            "Add it to bookshelf/.env"
        )

    # Detect image format for MIME type
    mime_type = _detect_mime_type(image_data)
    b64_image = base64.standard_b64encode(image_data).decode("utf-8")

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
                            "url": f"data:{mime_type};base64,{b64_image}",
                        },
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT,
                    },
                ],
            },
        ],
        "max_tokens": 2048,
        "temperature": 0.1,  # Low temperature for factual recognition
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ouroboros",
        "X-Title": "Bookshelf Catalog Bot",
    }

    logger.info(f"Calling VLM: {VLM_MODEL} (image: {len(image_data)} bytes, {mime_type})")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(OPENROUTER_URL, json=payload, headers=headers)

            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"OpenRouter API error {response.status_code}: {error_text}")
                raise RuntimeError(f"OpenRouter API error {response.status_code}: {error_text}")

            data = response.json()

        except httpx.TimeoutException:
            raise RuntimeError(f"VLM request timed out after {REQUEST_TIMEOUT}s")
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error calling VLM: {e}")

    # Extract text from response
    content = _extract_content(data)
    if not content:
        logger.warning("VLM returned empty response")
        return []

    logger.debug(f"VLM response: {content[:500]}")

    # Parse JSON from response
    books = _parse_books_json(content)
    logger.info(f"Recognized {len(books)} books")
    return books


def _detect_mime_type(image_data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if image_data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return "image/webp"
    elif image_data[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    else:
        # Default to JPEG (most common for Telegram photos)
        return "image/jpeg"


def _extract_content(response_data: dict) -> str:
    """Extract text content from OpenRouter API response."""
    try:
        choices = response_data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content.strip() if isinstance(content, str) else ""
    except Exception as e:
        logger.warning(f"Could not extract content from response: {e}")
        return ""


def _parse_books_json(text: str) -> list[dict]:
    """
    Parse JSON array of books from VLM response text.

    Handles:
    - Clean JSON: [{"title": "...", "author": "..."}]
    - JSON wrapped in markdown code blocks
    - JSON embedded in explanatory text
    """
    # Try direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _validate_books(data)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code blocks
    code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    match = re.search(code_block_pattern, text)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    # Try to extract bare JSON array from text
    array_pattern = r'\[\s*\{[\s\S]*?\}\s*\]'
    match = re.search(array_pattern, text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return _validate_books(data)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not parse books from VLM response: {text[:200]}")
    return []


def _validate_books(data: list) -> list[dict]:
    """
    Validate and normalize book list from VLM.
    Filters out items without a title.
    """
    books = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        author = str(item.get("author", "")).strip()
        if title:
            books.append({"title": title, "author": author})
    return books
