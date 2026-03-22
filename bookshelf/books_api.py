"""
books_api.py — Book enrichment via Google Books API

Takes a list of {title, author} dicts from the VLM and enriches them
with full metadata from Google Books API (free, no API key required).

Metadata returned per book:
    - Title (confirmed/corrected)
    - Author
    - Year (publication year)
    - ISBN (ISBN-13 preferred, ISBN-10 fallback)
    - Genre (categories from Google Books)
    - Pages (page count)
    - Cover URL (thumbnail URL)

Books are enriched concurrently for speed.
If a book is not found in Google Books API, the raw VLM data is returned.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
MAX_CONCURRENT_REQUESTS = 5  # Be polite to Google's free API
REQUEST_TIMEOUT = 10.0


async def enrich_books(raw_books: list[dict]) -> list[dict]:
    """
    Enrich a list of raw books with metadata from Google Books API.

    Args:
        raw_books: List of {title, author} dicts from vision.recognize_books()

    Returns:
        List of enriched book dicts with full metadata.
        Books not found in API still appear, but with minimal data.
    """
    if not raw_books:
        return []

    # Use semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        tasks = [
            _enrich_one(client, semaphore, book)
            for book in raw_books
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for book, result in zip(raw_books, results):
        if isinstance(result, Exception):
            logger.warning(f"Error enriching '{book.get('title', '?')}': {result}")
            # Use raw data as fallback
            enriched.append(_raw_to_enriched(book))
        else:
            enriched.append(result)

    return enriched


async def _enrich_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    raw_book: dict,
) -> dict:
    """Enrich a single book with Google Books API data."""
    async with semaphore:
        title = raw_book.get("title", "").strip()
        author = raw_book.get("author", "").strip()

        if not title:
            return _raw_to_enriched(raw_book)

        # Build search query
        query = f'intitle:"{title}"'
        if author:
            query += f' inauthor:"{author}"'

        params = {
            "q": query,
            "maxResults": 3,
            "langRestrict": None,  # Search all languages
            "printType": "books",
        }
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}

        logger.debug(f"Google Books search: {query}")

        try:
            response = await client.get(GOOGLE_BOOKS_URL, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Google Books API error for '{title}': {e}")
            return _raw_to_enriched(raw_book)
        except Exception as e:
            logger.warning(f"Error fetching '{title}': {e}")
            return _raw_to_enriched(raw_book)

        # Extract best match
        items = data.get("items", [])
        if not items:
            logger.info(f"No Google Books result for: {title}")
            return _raw_to_enriched(raw_book)

        # Pick the best matching result
        best = _pick_best_match(items, title, author)
        return _extract_metadata(best, raw_book)


def _pick_best_match(items: list[dict], title: str, author: str) -> dict:
    """
    Pick the best matching item from Google Books results.

    Scoring:
    - Title similarity (case-insensitive contains)
    - Author similarity
    - Prefers items with more complete data

    Returns the first (highest relevance) item if no better match found.
    """
    title_lower = title.lower()
    author_lower = author.lower()

    best_item = items[0]
    best_score = 0

    for item in items:
        info = item.get("volumeInfo", {})
        score = 0

        # Title match
        item_title = info.get("title", "").lower()
        if title_lower in item_title or item_title in title_lower:
            score += 2
        elif any(word in item_title for word in title_lower.split() if len(word) > 3):
            score += 1

        # Author match
        if author_lower:
            item_authors = [a.lower() for a in info.get("authors", [])]
            if any(author_lower in a or a in author_lower for a in item_authors):
                score += 2

        # Prefer items with ISBN
        if info.get("industryIdentifiers"):
            score += 1

        # Prefer items with page count
        if info.get("pageCount"):
            score += 0.5

        if score > best_score:
            best_score = score
            best_item = item

    return best_item


def _extract_metadata(item: dict, raw_book: dict) -> dict:
    """Extract metadata from a Google Books API volume item."""
    info = item.get("volumeInfo", {})

    # Title and Author
    title = info.get("title", raw_book.get("title", ""))
    subtitle = info.get("subtitle", "")
    if subtitle:
        title = f"{title}: {subtitle}"

    authors = info.get("authors", [])
    author = ", ".join(authors) if authors else raw_book.get("author", "")

    # Year
    published_date = info.get("publishedDate", "")
    year = _extract_year(published_date)

    # ISBN — prefer ISBN-13 over ISBN-10
    isbn = _extract_isbn(info.get("industryIdentifiers", []))

    # Genre (categories)
    categories = info.get("categories", [])
    genre = categories[0] if categories else ""

    # Pages
    pages = info.get("pageCount", "")

    # Cover URL
    image_links = info.get("imageLinks", {})
    cover_url = (
        image_links.get("thumbnail")
        or image_links.get("smallThumbnail")
        or ""
    )
    # Use HTTPS
    if cover_url:
        cover_url = cover_url.replace("http://", "https://")

    return {
        "Title": title,
        "Author": author,
        "Year": year,
        "ISBN": isbn,
        "Genre": genre,
        "Pages": str(pages) if pages else "",
        "Cover URL": cover_url,
    }


def _raw_to_enriched(raw_book: dict) -> dict:
    """Convert raw VLM data to enriched format with empty metadata fields."""
    return {
        "Title": raw_book.get("title", ""),
        "Author": raw_book.get("author", ""),
        "Year": "",
        "ISBN": "",
        "Genre": "",
        "Pages": "",
        "Cover URL": "",
    }


def _extract_year(published_date: str) -> str:
    """Extract year from various date formats: '2023', '2023-01', '2023-01-15'."""
    if not published_date:
        return ""

    # Try to extract 4-digit year
    import re
    match = re.search(r"\b(\d{4})\b", published_date)
    if match:
        year = int(match.group(1))
        # Sanity check: book year should be between 1000 and current+1
        current_year = datetime.now().year
        if 1000 <= year <= current_year + 1:
            return str(year)

    return ""


def _extract_isbn(identifiers: list[dict]) -> str:
    """
    Extract ISBN from Google Books industry identifiers.

    Prefers ISBN-13, falls back to ISBN-10.
    """
    isbn13 = ""
    isbn10 = ""

    for identifier in identifiers:
        id_type = identifier.get("type", "")
        id_value = identifier.get("identifier", "")

        if id_type == "ISBN_13":
            isbn13 = id_value
        elif id_type == "ISBN_10":
            isbn10 = id_value

    return isbn13 or isbn10
