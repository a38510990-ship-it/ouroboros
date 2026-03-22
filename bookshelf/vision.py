"""
Vision module: sends a bookshelf image to OpenRouter VLM and returns book list.
"""
import base64
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
VLM_MODEL = "google/gemini-2.0-flash-001"  # fast + cheap vision model

PROMPT = """You are a book cataloging assistant. 
Analyze this photo of a bookshelf carefully.

List EVERY book you can see. For each book extract from the spine:
- Title (as best as you can read)
- Author (if visible)

Return ONLY a JSON array, no markdown, no explanation. Format:
[
  {"title": "Book Title", "author": "Author Name"},
  {"title": "Another Book", "author": "Unknown"}
]

If you cannot see any books clearly, return an empty array: []
"""


def image_to_base64(image_path: str) -> str:
    """Read image file and encode to base64."""
    return base64.standard_b64encode(Path(image_path).read_bytes()).decode("utf-8")


def recognize_books(image_path: str, api_key: str) -> list[dict]:
    """
    Send image to OpenRouter VLM and return list of recognized books.
    
    Returns:
        List of dicts with 'title' and 'author' keys.
    """
    image_b64 = image_to_base64(image_path)

    # Detect mime type from extension
    ext = Path(image_path).suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/jpeg")

    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}"
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT,
                    },
                ],
            }
        ],
        "max_tokens": 2000,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/ouroboros",
        "X-Title": "Bookshelf Catalog Bot",
        "Content-Type": "application/json",
    }

    logger.info(f"Sending image to VLM ({VLM_MODEL})...")

    response = httpx.post(OPENROUTER_API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()

    logger.info(f"VLM response: {content[:200]}...")

    # Parse JSON — strip markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    import json
    try:
        books = json.loads(content)
        if not isinstance(books, list):
            logger.warning("VLM returned non-list JSON, wrapping in list")
            books = []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse VLM response as JSON: {e}\nContent: {content}")
        books = []

    logger.info(f"Recognized {len(books)} books")
    return books
